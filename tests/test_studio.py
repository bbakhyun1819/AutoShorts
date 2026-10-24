import copy
import io
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from chzzk_demo import assistant
from chzzk_demo.analyze import combine
from chzzk_demo.core import read, save
from chzzk_demo.pipeline import ASSET_RULES, PipelineManager
from chzzk_demo.render import validate_plan
from chzzk_demo.report import report


class FakeLibrary:
    """The catalog boundary; all workflow state and validation remain real."""

    def __init__(self):
        self.entries = {}
        self.token = "studio-test-token"
        self.added = []

    def add_plan(self, path):
        path = Path(path).resolve()
        existing = next((row for row in self.entries.values() if row["plan"] == path), None)
        if existing:
            return existing
        item = {"id": f"item-{len(self.entries) + 1}", "plan": path}
        self.entries[item["id"]] = item
        self.added.append(path)
        return item

    def list_items(self):
        return {"items": [{"id": row["id"], "title": " ".join(read(row["plan"])["title_lines"]),
                            "editable": True, "video_url": None} for row in self.entries.values()]}


class StudioTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="shorts studio ")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.video = self.workspace / "source.mp4"
        self.video.write_bytes(b"fixture source; never decoded")
        self.font = self.workspace / "font.ttf"
        self.font.write_bytes(b"fixture font; no rendering")
        self.logo = self.workspace / "logo.png"
        self.logo.write_bytes(b"fixture logo; no rendering")
        self.library = FakeLibrary()
        self.manager = PipelineManager(self.library, workspace=self.workspace)
        self.commands = []
        guard = patch("chzzk_demo.pipeline.subprocess.Popen", side_effect=AssertionError("A studio test tried to launch a subprocess"))
        guard.start()
        self.addCleanup(guard.stop)

    def create(self, **options):
        return self.manager.create({"title": "테스트 작업", "url": "https://chzzk.naver.com/video/15760891",
                                    "start": 10, "end": 100, **options})

    def prepared(self, **options):
        result = self.create(allow_no_chat=True, frames=0, **options)
        project = self.manager.projects[result["id"]]
        job = Path(project["job_path"])
        save(job / "job.json", dict(video=str(self.video), video_id="15760891", source_duration=120,
                                     start=10, end=100, url=project["config"]["url"], chat_offset=0,
                                     sync_status="unverified"))
        save(job / "transcript.json", [dict(start=10, end=12, text="첫 대사"),
                                        dict(start=60, end=62, text=" 마지막 반응.")])
        save(job / "chats.json", [])
        return project, job

    def candidates(self, job):
        rows = [dict(id="first", start=10, end=30, category="토크", reason="도입과 답변", sources=["transcript"], priority=4),
                dict(id="second", start=40, end=60, category="게임", reason="게임 반응", sources=["transcript"], priority=3),
                dict(id="third", start=70, end=95, category="토크", reason="마지막 대화", sources=["transcript"], priority=2)]
        save(job / "candidates.json", rows)
        return rows

    def wait(self, project_id):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            with self.manager.lock:
                project = copy.deepcopy(self.manager.projects[project_id])
                if project["task"]["state"] != "running" and self.manager.active is None:
                    return project
            time.sleep(.005)
        self.fail("The fixture workflow did not finish")

    def offline_run(self, project, step, command, *args):
        """Run only inexpensive JSON stages, using the actual assistant importer."""
        self.commands.append((step, command, args))
        self.manager._step(project, step, "running")
        job = Path(project["job_path"])
        with redirect_stdout(io.StringIO()):
            if command == "signals":
                save(job / "signal-candidates.json", [])
                save(job / "chat-bins.json", [])
            elif command == "assistant-prepare":
                stage = args[args.index("--stage") + 1]
                ids = [args[i + 1] for i, value in enumerate(args) if value == "--candidate-id"]
                assistant.prepare(SimpleNamespace(job=job, stage=stage, frames=0,
                                                   limit=int(args[args.index("--limit") + 1]) if "--limit" in args else 10,
                                                   from_source="--from-source" in args, candidate_id=ids or None))
            elif command == "assistant-import":
                assistant.import_result(SimpleNamespace(job=job, input=args[args.index("--input") + 1]))
            elif command == "combine":
                combine(SimpleNamespace(job=job))
            elif command == "report":
                report(SimpleNamespace(job=job))
            elif command == "sync":
                value = read(job / "job.json")
                value.update(chat_offset=args[args.index("--offset") + 1], sync_status="confirmed")
                save(job / "job.json", value)
            else:
                raise AssertionError(f"Unexpected expensive stage: {command}")
        self.manager._step(project, step, "done")

    def test_create_requires_one_source_and_valid_ranges_and_local_chat_choice(self):
        invalid = [{}, {"url": "https://example.com/video/123"}, {"url": "https://chzzk.naver.com/clips/abc"},
                   {"url": "https://chzzk.naver.com/video/1", "local_video": str(self.video)},
                   {"local_video": str(self.video)}, {"local_video": "missing.mp4", "allow_no_chat": True}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.manager.create(payload)
        for update in [dict(start=-1), dict(start=True), dict(start=10.5), dict(end=10),
                       dict(end=float("inf")), dict(end=864001)]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.create(**update)
        remote = self.create()
        local = self.manager.create(dict(local_video=str(self.video), allow_no_chat=True, start=0, end=15))
        self.assertEqual((remote["start"], remote["end"]), (10, 100))
        self.assertEqual(local["config"]["local_video"], str(self.video))
        self.assertEqual(local["config"]["url"], "")
        self.assertFalse(Path(remote["job_path"]).exists())
        self.assertEqual(self.video.read_bytes(), b"fixture source; never decoded")

    def test_create_validates_length_limits_after_merging_defaults(self):
        for payload in [dict(min_length=120), dict(max_length=40), dict(min_length=110, max_length=60)]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.create(**payload)
        result = self.create(min_length=120, max_length=150)
        self.assertEqual((result["config"]["min_length"], result["config"]["max_length"]), (120, 150))

    def test_existing_job_import_deduplicates_resolved_paths_and_preserves_artifacts(self):
        job = self.workspace / "existing"
        save(job / "job.json", dict(video=str(self.video), video_id="local", start=5, end=90,
                                     source_duration=120, sync_status="confirmed"))
        save(job / "transcript.json", [dict(start=5, end=6, text="이미 전사함")])
        save(job / "chats.json", [])
        save(job / "signal-candidates.json", [])
        save(job / "candidates.json", [])
        before = {p.name: p.read_bytes() for p in job.iterdir()}
        first = self.manager.create({"job_path": "existing"})
        second = self.manager.create({"job_path": str(job), "title": "이름을 바꾸지 않음"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.manager.projects), 1)
        self.assertEqual((first["start"], first["end"]), (5, 90))
        steps = {row["key"]: row["state"] for row in first["steps"]}
        self.assertEqual([steps[key] for key in ("ingest", "chats", "transcribe", "sync", "signals")], ["done"] * 5)
        self.assertEqual({p.name: p.read_bytes() for p in job.iterdir()}, before)
        with self.assertRaises(ValueError):
            self.manager.create({"job_path": "missing-job"})

    def test_global_defaults_validate_scope_persist_and_do_not_rewrite_existing_project(self):
        existing = self.create()
        changes = dict(action="save-defaults", channel="새 채널", min_length=70, max_length=100,
                       render_count=3, review_limit=12, frames=0, provider="assistant")
        self.manager.action("", changes)
        self.assertEqual(self.manager.projects[existing["id"]]["config"]["render_count"], 1)
        created = self.create()
        self.assertEqual(created["config"]["render_count"], 3)
        restarted = PipelineManager(self.library, workspace=self.workspace)
        self.assertEqual(restarted.defaults["channel"], "새 채널")
        self.assertEqual(restarted.defaults["min_length"], 70)
        settings = self.manager.root / "settings.json"
        before = settings.read_bytes()
        for invalid in [dict(url="https://chzzk.naver.com/video/1"), dict(job_path="outside"),
                        dict(cookies_path="cookie.txt"), dict(min_length=110), dict(frames=25),
                        dict(render_count=0), dict(provider="unknown")]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.manager.action("", {"action": "save-defaults", **invalid})
            self.assertEqual(settings.read_bytes(), before)

    def test_upload_rejects_unsafe_types_size_invalid_json_and_truncation_without_using_filename_as_path(self):
        class Unreadable:
            def read(self, *_args):
                raise AssertionError("An invalid upload was consumed before validation")

        for length, kind, name in [(0, "response", "a.json"), (-1, "response", "a.json"),
                                    (ASSET_RULES["response"][1] + 1, "response", "a.json"),
                                    (1, "unknown", "a.json"), (1, "video", "code.exe"), (1, "font", "a.json")]:
            with self.subTest(kind=kind, name=name, length=length), self.assertRaises(ValueError):
                self.manager.upload(Unreadable(), length, kind, name)
        content = b'{"stage": "text"}'
        result = self.manager.upload(io.BytesIO(content), len(content), "response", "../../escape.json")
        target = Path(result["path"])
        self.assertEqual(target.parent, self.manager.root / "uploads")
        self.assertEqual(result["name"], "escape.json")
        self.assertEqual(target.read_bytes(), content)
        for data, length in [(b"broken json", 11), (b"{}", 5)]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.manager.upload(io.BytesIO(data), length, "response", "bad.json")
        self.assertEqual(list(target.parent.iterdir()), [target])
        self.assertFalse((self.workspace / "escape.json").exists())

    def test_async_failure_retry_and_cross_project_concurrency_guard(self):
        first, second = self.create(), self.create(title="다른 작업")
        project = self.manager.projects[first["id"]]
        entered, release = threading.Event(), threading.Event()

        def fail_stage(value):
            self.manager._step(value, "ingest", "running")
            entered.set()
            if not release.wait(3):
                raise AssertionError("Fixture release timeout")
            raise ValueError("fixture ingest failure")

        with patch.object(self.manager, "_prepare", side_effect=fail_stage):
            try:
                started = self.manager.action(first["id"], {"action": "prepare"})
                self.assertEqual(started["task"]["state"], "running")
                self.assertTrue(entered.wait(2))
                for project_id in (first["id"], second["id"]):
                    with self.subTest(project_id=project_id), self.assertRaises(ValueError):
                        self.manager.action(project_id, {"action": "prepare"})
            finally:
                release.set()
            failed = self.wait(first["id"])
        self.assertEqual((failed["state"], failed["task"]["state"]), ("error", "error"))
        self.assertIn("fixture ingest failure", failed["error"])
        self.assertEqual(next(row["state"] for row in failed["steps"] if row["key"] == "ingest"), "error")
        with patch.object(self.manager, "_prepare", side_effect=lambda value: self.manager._step(value, "ingest", "done")):
            self.manager.action(first["id"], {"action": "prepare"})
            retried = self.wait(first["id"])
        self.assertEqual((retried["state"], retried["task"]["state"]), ("ready", "done"))
        self.assertIsNone(retried["error"])
        self.assertEqual(read(self.manager.projects_root / project["id"] / "project.json")["task"]["state"], "done")

    def test_restart_marks_persisted_running_work_interrupted_and_preserves_completed_steps(self):
        created = self.create()
        project = self.manager.projects[created["id"]]
        project.update(state="running", task={"state": "running", "action": "prepare"})
        self.manager._step(project, "ingest", "done")
        self.manager._step(project, "transcribe", "running")
        restarted = PipelineManager(self.library, workspace=self.workspace)
        current = restarted.projects[created["id"]]
        self.assertEqual((current["state"], current["task"]["state"]), ("interrupted", "interrupted"))
        self.assertEqual({row["key"]: row["state"] for row in current["steps"]}["ingest"], "done")
        self.assertEqual({row["key"]: row["state"] for row in current["steps"]}["transcribe"], "interrupted")
        self.assertIsNone(restarted.active)
        self.assertEqual(read(restarted.projects_root / created["id"] / "project.json")["state"], "interrupted")

    def test_gemini_requires_explicit_external_consent_for_analysis_and_review(self):
        project, _ = self.prepared(provider="gemini")
        with patch.object(self.manager, "_start", return_value={"queued": True}) as start:
            for action in ("analyze", "prepare-review"):
                for consent in (None, False, "true", 1):
                    with self.subTest(action=action, consent=consent), self.assertRaisesRegex(ValueError, "Gemini"):
                        self.manager.action(project["id"], {"action": action, "allow_external": consent})
                start.assert_not_called()
                self.assertEqual(self.manager.action(project["id"], {"action": action, "allow_external": True}), {"queued": True})
                start.assert_called_once()
                start.reset_mock()

    def test_assistant_prepare_creates_waiting_packet_without_claiming_analysis_completed(self):
        project, job = self.prepared(render_count=3, request="도입과 마지막 반응을 확인")
        with patch.object(self.manager, "_run", side_effect=self.offline_run):
            self.manager.action(project["id"], {"action": "analyze"})
            current = self.wait(project["id"])
        self.assertEqual((current["state"], current["task"]["state"]), ("waiting_analysis", "done"))
        self.assertEqual({row["key"]: row["state"] for row in current["steps"]}["analysis"], "waiting")
        folder = Path(current["packets"]["text"]["path"])
        self.assertFalse(read(folder / "response-template.json")["complete"])
        self.assertFalse((job / "text-candidates.json").exists())
        self.assertFalse((job / "candidates.json").exists())
        detail = self.manager.detail(project["id"])
        self.assertIn("우선 제작 수: 3개", detail["packets"]["text"]["request"])
        self.assertEqual([row[1] for row in self.commands], ["signals", "assistant-prepare"])

    def test_prepare_reuses_existing_transcript_and_source_without_running_ingest_or_asr(self):
        project, job = self.prepared()
        self.candidates(job)
        before = {path.name: path.read_bytes() for path in job.iterdir() if path.is_file()}
        with patch.object(self.manager, "_run", side_effect=AssertionError("Existing data should be reused")) as run:
            for _ in range(2):
                self.manager.action(project["id"], {"action": "prepare"})
                current = self.wait(project["id"])
                self.assertEqual(current["state"], "ready_for_analysis")
                self.assertEqual(current["task"]["state"], "done")
            run.assert_not_called()
        self.assertEqual({path.name: path.read_bytes() for path in job.iterdir() if path.is_file()}, before)
        self.assertEqual(self.video.read_bytes(), b"fixture source; never decoded")
        steps = {row["key"]: row["state"] for row in current["steps"]}
        self.assertEqual((steps["ingest"], steps["transcribe"], steps["chats"], steps["sync"]),
                         ("done", "done", "skipped", "skipped"))

    def test_changed_analysis_request_marks_steps_pending_and_rejects_previous_response_as_stale(self):
        project, job = self.prepared(request="첫 번째 요청")
        with patch.object(self.manager, "_run", side_effect=self.offline_run):
            self.manager.action(project["id"], {"action": "analyze"})
            prepared = self.wait(project["id"])
            folder = Path(prepared["packets"]["text"]["path"])
            response = read(folder / "response-template.json")
            response.update(complete=True, reviewed_windows=[w["id"] for w in read(folder / "packet.json")["windows"]], candidates=[])
            self.manager.action(project["id"], {"action": "import-text", "response": response})
            self.assertEqual(self.wait(project["id"])["state"], "no_candidates")
            before = {name: (job / name).read_bytes() for name in ("text-candidates.json", "text-analysis.json")}
            updated = self.manager.action(project["id"], {"action": "settings", "config": {"request": "새로운 결말 중심 요청"}})
            self.assertEqual(updated["state"], "ready_for_analysis")
            steps = {row["key"]: row["state"] for row in updated["steps"]}
            self.assertEqual((steps["analysis"], steps["review"]), ("pending", "pending"))
            self.assertEqual(read(job / "studio-request.json")["request"], "새로운 결말 중심 요청")
            self.manager.action(project["id"], {"action": "import-text", "response": response})
            failed = self.wait(project["id"])
            self.assertEqual(failed["state"], "error")
            self.assertIn("assistant-prepare", failed["error"])
            self.assertEqual({name: (job / name).read_bytes() for name in before}, before)
            self.manager.action(project["id"], {"action": "analyze"})
            renewed = self.wait(project["id"])
        self.assertEqual(renewed["state"], "waiting_analysis")
        self.assertNotEqual(renewed["packets"]["text"]["path"], str(folder))

    def test_sync_requires_human_confirmation_before_analysis_with_chat(self):
        project, job = self.prepared()
        project["config"]["allow_no_chat"] = False
        with patch.object(self.manager, "_run", side_effect=self.offline_run):
            self.manager.action(project["id"], {"action": "analyze"})
            current = self.wait(project["id"])
            self.assertEqual(current["state"], "error")
            self.assertEqual(self.commands, [])
            with self.assertRaisesRegex(ValueError, "직접 비교"):
                self.manager.action(project["id"], {"action": "confirm-sync", "confirmed": False, "offset": 3})
            self.manager.action(project["id"], {"action": "confirm-sync", "confirmed": True, "offset": 3})
            confirmed = self.wait(project["id"])
            self.assertEqual(confirmed["state"], "ready_for_analysis")
            self.assertEqual(read(job / "job.json")["sync_status"], "confirmed")
            self.assertEqual(self.commands[0], ("sync", "sync", ("--offset", 3.0, "--confirmed")))
            self.manager.action(project["id"], {"action": "analyze"})
            self.assertEqual(self.wait(project["id"])["state"], "waiting_analysis")

    def test_changed_imported_chat_requires_sync_again_but_identical_chat_keeps_confirmation(self):
        project, job = self.prepared()
        current_chat = [{"id": "0", "time": 20.0, "text": "원래 채팅"}]
        external_chat = self.workspace / "chat-input.json"
        save(external_chat, current_chat)
        save(job / "chats.json", current_chat)
        config = read(job / "job.json")
        config.update(sync_status="confirmed", chat_offset=3)
        save(job / "job.json", config)
        project["config"].update(allow_no_chat=False, chat_path=str(external_chat))
        for key in ("analysis", "review"):
            self.manager._step(project, key, "done")
        save(job / "text-candidates.json", [{"reason": "이전 분석은 보존"}])
        before = (job / "text-candidates.json").read_bytes()

        def sync_report_only(value, step, command, *args):
            self.assertEqual((step, command, args), ("sync", "sync-report", ()))
            self.manager._step(value, step, "done")

        with patch.object(self.manager, "_run", side_effect=sync_report_only):
            self.manager.action(project["id"], {"action": "prepare"})
            same = self.wait(project["id"])
            self.assertEqual(same["state"], "ready_for_analysis")
            self.assertEqual(read(job / "job.json")["sync_status"], "confirmed")
            self.assertEqual({r["key"]: r["state"] for r in same["steps"]}["analysis"], "done")
            save(external_chat, [{"time": 50, "text": "변경된 채팅"}])
            self.manager.action(project["id"], {"action": "prepare"})
            changed = self.wait(project["id"])
        self.assertEqual(changed["state"], "waiting_sync")
        self.assertEqual(read(job / "job.json")["sync_status"], "unverified")
        states = {r["key"]: r["state"] for r in changed["steps"]}
        self.assertEqual((states["sync"], states["analysis"], states["review"]), ("waiting", "pending", "pending"))
        self.assertEqual((job / "text-candidates.json").read_bytes(), before)

    def test_changed_sync_offset_invalidates_completed_analysis_and_review_without_deleting_outputs(self):
        project, job = self.prepared()
        for key in ("analysis", "review"):
            self.manager._step(project, key, "done")
        save(job / "text-candidates.json", [{"reason": "이전 분석 보존"}])
        self.candidates(job)
        before = {name: (job / name).read_bytes() for name in ("text-candidates.json", "candidates.json")}
        with patch.object(self.manager, "_run", side_effect=self.offline_run):
            self.manager.action(project["id"], {"action": "confirm-sync", "confirmed": True, "offset": 5})
            current = self.wait(project["id"])
        self.assertEqual(current["state"], "ready_for_analysis")
        states = {row["key"]: row["state"] for row in current["steps"]}
        self.assertEqual((states["sync"], states["analysis"], states["review"]), ("done", "pending", "pending"))
        self.assertEqual(read(job / "job.json")["chat_offset"], 5)
        self.assertEqual({name: (job / name).read_bytes() for name in before}, before)

    def test_import_uses_actual_assistant_validation_and_zero_candidates_completes_without_clips(self):
        project, job = self.prepared()
        with patch.object(self.manager, "_run", side_effect=self.offline_run):
            self.manager.action(project["id"], {"action": "analyze"})
            current = self.wait(project["id"])
            folder = Path(current["packets"]["text"]["path"])
            response = read(folder / "response-template.json")
            save(job / "text-candidates.json", [{"reason": "이전 결과"}])
            before = (job / "text-candidates.json").read_bytes()
            self.manager.action(project["id"], {"action": "import-text", "response": response})
            failed = self.wait(project["id"])
            self.assertEqual(failed["state"], "error")
            self.assertIn("분석 대기", failed["error"])
            self.assertEqual((job / "text-candidates.json").read_bytes(), before)
            self.assertFalse((job / "candidates.json").exists())
            response.update(complete=True, reviewed_windows=[w["id"] for w in read(folder / "packet.json")["windows"]], candidates=[])
            self.manager.action(project["id"], {"action": "import-text", "response": response})
            current = self.wait(project["id"])
            self.assertEqual(current["state"], "no_candidates")
            self.assertEqual(read(job / "candidates.json"), [])
            self.assertTrue((job / "report.html").is_file())
            command_count = len(self.commands)
            self.manager.action(project["id"], {"action": "prepare-review"})
            reviewed = self.wait(project["id"])
        self.assertEqual(reviewed["state"], "no_candidates")
        self.assertEqual({row["key"]: row["state"] for row in reviewed["steps"]}["review"], "skipped")
        self.assertEqual(len(self.commands), command_count)
        self.assertEqual([row[1] for row in self.commands][-3:], ["assistant-import", "combine", "report"])
        self.assertFalse((job / "clips").exists())

    def test_review_prepares_only_selected_candidates_and_rejects_unknown_or_duplicate_ids(self):
        project, job = self.prepared()
        candidates = self.candidates(job)
        before = (job / "candidates.json").read_bytes()
        with patch.object(self.manager, "_run", side_effect=self.offline_run):
            for ids in ([], ["unknown"], ["first", "first"], "first", [1]):
                with self.subTest(ids=ids), self.assertRaises(ValueError):
                    self.manager._review(project, {"candidate_ids": ids})
            self.assertEqual(self.commands, [])
            self.manager.action(project["id"], {"action": "prepare-review", "candidate_ids": ["third"]})
            reviewed = self.wait(project["id"])
        self.assertEqual(reviewed["state"], "waiting_review")
        packet = read(Path(reviewed["packets"]["review"]["path"]) / "packet.json")
        self.assertEqual([row["candidate_id"] for row in packet["items"]], ["third"])
        args = self.commands[0][2]
        self.assertEqual(args[args.index("--limit") + 1], 1)
        self.assertEqual(args[args.index("--candidate-id") + 1], "third")
        self.assertIn("--from-source", args)
        self.assertEqual((job / "candidates.json").read_bytes(), before)
        self.assertFalse((job / "clips").exists())

    def test_create_plan_groups_captions_with_source_times_and_registers_without_touching_analysis(self):
        project, job = self.prepared(channel="채널", font=str(self.font), logo=str(self.logo))
        self.candidates(job)
        words = [dict(start=9, end=11, text="도입 "), dict(start=10.5, end=12, text="겹친 단어."),
                 dict(start=14, end=15, text="다음\n문장 "), dict(start=15, end=16, text="끝!"),
                 dict(start=29, end=31, text="마지막."), dict(start=31, end=32, text="범위 밖")]
        save(job / "transcript.json", words)
        before = {name: (job / name).read_bytes() for name in ("job.json", "transcript.json", "candidates.json")}

        def validate(path):
            plan = read(path)
            return validate_plan(plan, source_duration=120, source_width=1920, source_height=1080), {}, {}

        with patch("chzzk_demo.render.load_render_plan", side_effect=validate):
            result = self.manager.action(project["id"], {"action": "create-plan", "candidate_id": "first", "title_lines": ["도입부터 결말"]})
            with self.assertRaises(ValueError):
                self.manager.action(project["id"], {"action": "create-plan", "candidate_id": "unknown"})
            with self.assertRaises(ValueError):
                self.manager.action(project["id"], {"action": "create-plan", "candidate_id": "first", "title_lines": ["1", "2", "3"]})
        self.assertEqual(len(self.library.added), 1)
        plan_path = self.library.added[0]
        plan = read(plan_path)
        self.assertEqual((plan["source"], plan["source_start"], plan["keep"]), (str(self.video), 0, [[10, 30]]))
        self.assertEqual([(c["start"], c["end"]) for c in plan["captions"]], [(10, 12), (14, 16), (29, 30)])
        self.assertEqual(plan["captions"][1]["text"], "다음 문장 끝!")
        self.assertEqual(result["project"]["state"], "editing")
        self.assertEqual(result["project"]["item_ids"], [result["item_id"]])
        self.assertEqual(list(plan_path.parent.glob("*.json")), [plan_path])
        self.assertEqual({name: (job / name).read_bytes() for name in before}, before)

    def test_import_plan_keeps_project_source_identity_and_removes_invalid_draft(self):
        project, job = self.prepared(channel="채널", font=str(self.font), logo=str(self.logo))
        base = dict(source=str(self.video), source_start=0, slug="imported", logo=str(self.logo), font=str(self.font),
                    font_name="Fixture", channel="채널", title_lines=["가져온 편집안"], keep=[[10, 30]], captions=[])
        with patch("chzzk_demo.render.load_render_plan", side_effect=lambda path:
                   (validate_plan(read(path), source_duration=120, source_width=1920, source_height=1080), {}, {})):
            for change in (dict(source=str(self.workspace / "other.mp4")), dict(source_start=10), dict(keep=[[10, 130]])):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    self.manager.action(project["id"], {"action": "import-plan", "plan": {**base, **change}})
            self.assertEqual(self.library.added, [])
            result = self.manager.action(project["id"], {"action": "import-plan", "plan": base})
        self.assertEqual(result["project"]["state"], "editing")
        self.assertEqual(read(self.library.added[0])["source"], str(self.video))
        self.assertEqual(len(list(self.library.added[0].parent.glob("*.json"))), 1)
        self.assertEqual(read(job / "job.json")["video"], str(self.video))

    def test_project_file_whitelist_rejects_traversal_even_inside_a_candidate_id(self):
        project, job = self.prepared()
        self.candidates(job)
        clip = job / "clips" / "first.mp4"
        clip.parent.mkdir()
        clip.write_bytes(b"known candidate")
        self.assertEqual(self.manager.file_path(project["id"], "source"), self.video)
        self.assertEqual(self.manager.file_path(project["id"], "transcript.json"), job / "transcript.json")
        self.assertEqual(self.manager.file_path(project["id"], "clip:first"), clip)
        save(job / "candidates.json", [dict(id="../../private")])
        for name in ("../job.json", "../../settings.json", "clip:../../private", "clip:unknown", "sync-3", str(self.video)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.manager.file_path(project["id"], name)
        with self.assertRaises(ValueError):
            self.manager.file_path("../project", "source")


if __name__ == "__main__":
    unittest.main()
