import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from chzzk_demo.cli import main
from chzzk_demo.workflow_lock import studio_job_lock


HOLDER = """
import sys
from pathlib import Path
from chzzk_demo.workflow_lock import studio_job_lock
with studio_job_lock(sys.argv[1]):
    Path(sys.argv[2]).touch()
    sys.stdin.readline()
"""
PROBE = """
import sys
from chzzk_demo.workflow_lock import studio_job_lock
with studio_job_lock(sys.argv[1]):
    print('acquired')
"""


class WorkflowLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="shorts worker lock ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.job = self.root / "job"
        self.environment = dict(os.environ, AUTOSHORTS_STUDIO_WORKER="1", PYTHONIOENCODING="utf-8")
        source = str(Path(__file__).resolve().parents[1] / "src")
        self.environment["PYTHONPATH"] = source + os.pathsep + self.environment.get("PYTHONPATH", "")

    @contextmanager
    def holder(self):
        ready = self.root / "holder-ready"
        process = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(self.job), str(ready)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=self.environment,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(ready.exists(), "The fixture worker failed to acquire its lock")
            yield process
        finally:
            try:
                process.communicate(input="release\n" if process.poll() is None else None, timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)

    def probe(self, job=None):
        return subprocess.run(
            [sys.executable, "-c", PROBE, str(job or self.job)],
            capture_output=True, text=True, encoding="utf-8", env=self.environment, timeout=5,
        )

    def test_same_job_rejected_across_processes_while_different_job_is_allowed(self):
        with self.holder() as process:
            blocked = self.probe(self.job / ".")
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("이전 작업이 아직 실행 중", blocked.stderr)
            self.assertIsNone(process.poll())
            allowed = self.probe(self.root / "another-job")
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertTrue((self.job / ".studio-stage.lock").is_file())
        self.assertEqual(self.probe().returncode, 0)

    def test_normal_exit_and_exception_release_the_lock_without_deleting_its_file(self):
        with patch.dict(os.environ, {"AUTOSHORTS_STUDIO_WORKER": "1"}):
            with studio_job_lock(self.job):
                lock_file = self.job / ".studio-stage.lock"
                identity = lock_file.stat().st_ino
                self.assertNotEqual(self.probe().returncode, 0)
            self.assertEqual(self.probe().returncode, 0)
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                with studio_job_lock(self.job):
                    raise RuntimeError("fixture failure")
            self.assertEqual(self.probe().returncode, 0)
            self.assertTrue(lock_file.is_file())
            self.assertEqual(lock_file.stat().st_ino, identity)

    def test_abrupt_worker_exit_releases_os_lock_without_stale_lock_cleanup(self):
        with self.holder() as process:
            self.assertNotEqual(self.probe().returncode, 0)
            process.kill()
            process.wait(timeout=5)
            self.assertTrue((self.job / ".studio-stage.lock").is_file())
            acquired = self.probe()
            self.assertEqual(acquired.returncode, 0, acquired.stderr)

    def test_cli_holds_lock_until_its_command_finishes(self):
        def report(_args):
            blocked = self.probe()
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("이전 작업이 아직 실행 중", blocked.stderr)

        with patch.dict(os.environ, {"AUTOSHORTS_STUDIO_WORKER": "1"}), \
                patch("sys.argv", ["chzzk-demo", "report", "--job", str(self.job)]), \
                patch("chzzk_demo.report.report", side_effect=report) as command:
            main()
        command.assert_called_once()
        self.assertEqual(self.probe().returncode, 0)

    def test_cli_refuses_busy_job_before_dispatch_with_a_korean_error(self):
        error = io.StringIO()
        with self.holder(), patch.dict(os.environ, {"AUTOSHORTS_STUDIO_WORKER": "1"}), \
                patch("sys.argv", ["chzzk-demo", "report", "--job", str(self.job)]), \
                patch("chzzk_demo.report.report") as command, redirect_stderr(error):
            with self.assertRaises(SystemExit) as failed:
                main()
            self.assertEqual(failed.exception.code, 1)
            command.assert_not_called()
        self.assertIn("실패: 이 작업 폴더에서 이전 작업이 아직 실행 중", error.getvalue())

    def test_cli_error_releases_lock_for_a_retry(self):
        with patch.dict(os.environ, {"AUTOSHORTS_STUDIO_WORKER": "1"}), \
                patch("sys.argv", ["chzzk-demo", "report", "--job", str(self.job)]), \
                patch("chzzk_demo.report.report", side_effect=ValueError("fixture command error")), \
                redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main()
        self.assertEqual(self.probe().returncode, 0)

    def test_plain_cli_ignores_worker_lock_and_does_not_create_lock_files(self):
        with self.holder():
            for flag in (None, "0", "true"):
                with self.subTest(flag=flag), patch.dict(os.environ):
                    os.environ.pop("AUTOSHORTS_STUDIO_WORKER", None)
                    if flag is not None:
                        os.environ["AUTOSHORTS_STUDIO_WORKER"] = flag
                    for job in (self.job, self.root / "ordinary-job"):
                        with patch("sys.argv", ["chzzk-demo", "report", "--job", str(job)]), \
                                patch("chzzk_demo.report.report") as command:
                            main()
                        command.assert_called_once()
                    self.assertFalse((self.root / "ordinary-job").exists())

    def test_worker_commands_without_job_do_not_create_a_lock(self):
        with patch.dict(os.environ, {"AUTOSHORTS_STUDIO_WORKER": "1"}), \
                patch("chzzk_demo.workflow_lock.Path") as path:
            with studio_job_lock(None):
                pass
            path.assert_not_called()


if __name__ == "__main__":
    unittest.main()
