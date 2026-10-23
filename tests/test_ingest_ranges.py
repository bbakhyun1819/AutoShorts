import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from chzzk_demo.cli import main
from chzzk_demo.core import read
from chzzk_demo.ingest import ingest


class IngestRangeTests(unittest.TestCase):
    @staticmethod
    def media_info(duration=20000):
        return {
            "format": {"duration": str(duration)},
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        }

    @staticmethod
    def args(job, **overrides):
        values = dict(job=str(job), url="https://chzzk.naver.com/video/15760891",
                      local_video=None, start=2880, duration=14520,
                      cookies=None, format=None)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_cli_default_analysis_duration_remains_two_hours(self):
        argv = ["chzzk-demo", "ingest", "--url",
                "https://chzzk.naver.com/video/15760891"]
        with patch("sys.argv", argv), patch("chzzk_demo.media.require_tools"), \
                patch("chzzk_demo.ingest.ingest") as ingest_mock:
            main()
        self.assertEqual(ingest_mock.call_args.args[0].duration, 7200)
        self.assertIsNone(ingest_mock.call_args.args[0].format)

    def test_cli_long_range_and_format_preserve_full_downloaded_source(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder) / "job"
            source = job / "source.mp4"

            def download(command):
                source.write_bytes(b"complete source stand-in")
                listing = Path(command[command.index("--print-to-file") + 2])
                listing.write_text(str(source) + "\n", encoding="utf-8")

            argv = ["chzzk-demo", "ingest", "--job", str(job), "--url",
                    "https://chzzk.naver.com/video/15760891", "--start", "2880",
                    "--duration", "14520", "--format", "best[height<=480]"]
            with patch("sys.argv", argv), patch("chzzk_demo.media.require_tools"), \
                    patch("chzzk_demo.ingest.run", side_effect=download) as run_mock, \
                    patch("chzzk_demo.ingest.probe", return_value=self.media_info()) as probe_mock, \
                    contextlib.redirect_stdout(io.StringIO()):
                main()

            command = run_mock.call_args.args[0]
            self.assertEqual(command[command.index("--format") + 1], "best[height<=480]")
            self.assertNotIn("--download-sections", command)
            self.assertNotIn("--postprocessor-args", command)
            probe_mock.assert_called_once_with(source.resolve())
            config = read(job / "job.json")
            self.assertEqual((config["start"], config["end"]), (2880, 17400))
            self.assertEqual(config["source_duration"], 20000)
            self.assertEqual(config["video"], str(source.resolve()))
            self.assertEqual(config["video_id"], "15760891")
            self.assertEqual(source.read_bytes(), b"complete source stand-in")

    def test_local_video_accepts_long_range_without_downloading_or_modifying_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "local.mp4"
            source.write_bytes(b"existing local source")
            job = Path(folder) / "job"
            with patch("chzzk_demo.ingest.run") as run_mock, \
                    patch("chzzk_demo.ingest.probe", return_value=self.media_info()), \
                    contextlib.redirect_stdout(io.StringIO()):
                ingest(self.args(job, local_video=str(source), url=None))
            run_mock.assert_not_called()
            config = read(job / "job.json")
            self.assertEqual((config["start"], config["end"]), (2880, 17400))
            self.assertEqual(config["video_id"], "local")
            self.assertEqual(config["video"], str(source.resolve()))
            self.assertEqual(source.read_bytes(), b"existing local source")

    def test_analysis_end_is_clamped_to_source_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "local.mp4"
            source.touch()
            job = Path(folder) / "job"
            with patch("chzzk_demo.ingest.run") as run_mock, \
                    patch("chzzk_demo.ingest.probe", return_value=self.media_info(10000)), \
                    contextlib.redirect_stdout(io.StringIO()):
                ingest(self.args(job, local_video=str(source), url=None))
            run_mock.assert_not_called()
            config = read(job / "job.json")
            self.assertEqual((config["start"], config["end"]), (2880, 10000))
            self.assertEqual(config["source_duration"], 10000)

    def test_invalid_range_is_rejected_before_download_or_probe(self):
        for overrides in ({"duration": 0}, {"duration": -1}, {"start": -1}):
            with self.subTest(**overrides), tempfile.TemporaryDirectory() as folder:
                job = Path(folder) / "job"
                with patch("chzzk_demo.ingest.run") as run_mock, \
                        patch("chzzk_demo.ingest.probe") as probe_mock, \
                        self.assertRaises(ValueError):
                    ingest(self.args(job, **overrides))
                run_mock.assert_not_called()
                probe_mock.assert_not_called()
                self.assertFalse((job / "job.json").exists())

    def test_start_at_or_beyond_source_end_is_rejected(self):
        for start in (20000, 20001):
            with self.subTest(start=start), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "local.mp4"
                source.touch()
                job = Path(folder) / "job"
                with patch("chzzk_demo.ingest.run") as run_mock, \
                        patch("chzzk_demo.ingest.probe", return_value=self.media_info()), \
                        self.assertRaises(ValueError):
                    ingest(self.args(job, local_video=str(source), url=None, start=start))
                run_mock.assert_not_called()
                self.assertFalse((job / "job.json").exists())


if __name__ == "__main__":
    unittest.main()
