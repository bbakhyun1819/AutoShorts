import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from chzzk_demo import assistant
from chzzk_demo.analyze import combine
from chzzk_demo.cli import main
from chzzk_demo.core import read, save
from chzzk_demo.report import report


class AssistantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.job = Path(temporary.name)
        self.config = dict(start=100, end=1350, video_id="123", chat_offset=3)
        save(self.job / "job.json", self.config)
        self.words = [
            dict(start=90, end=99, text="범위 이전"),
            dict(start=100, end=105, text="시작"),
            dict(start=695, end=705, text="경계를 잇는 대화"),
            dict(start=1310, end=1320, text="마지막 이야기"),
            dict(start=1349, end=1350, text="끝"),
            dict(start=1350, end=1355, text="범위 이후"),
        ]
        save(self.job / "transcript.json", self.words)
        save(self.job / "chat-bins.json", [dict(time=700, count=4)])
        save(self.job / "signal-candidates.json", [dict(start=690, end=740)])
        self.candidates = [
            dict(id="first", start=110, end=170, category="토크", reason="대화",
                 sources=["transcript", "assistant"], priority=4),
            dict(id="second", start=400, end=440, category="게임", reason="사건",
                 sources=["audio"], priority=3),
        ]
        save(self.job / "candidates.json", self.candidates)
        save(self.job / "chats.json", [dict(time=108, text="보정 후 클립 안"),
                                       dict(time=169, text="보정 후 클립 밖")])

    def prepare(self, stage="text", **kwargs):
        options = dict(job=self.job, stage=stage, limit=10, frames=0)
        options.update(kwargs)
        with redirect_stdout(io.StringIO()):
            return assistant.prepare(SimpleNamespace(**options))

    def text_response(self, folder, candidates=None):
        response = read(folder / "response-template.json")
        response.update(complete=True,
                        reviewed_windows=[w["id"] for w in read(folder / "packet.json")["windows"]],
                        candidates=[] if candidates is None else candidates)
        return response

    def candidate(self, **kwargs):
        result = dict(window_id="window-000", start=120, end=150,
                      category="토크", reason="대본에서 확인한 반전 대화")
        result.update(kwargs)
        return result

    def review_response(self, folder):
        response = read(folder / "response-template.json")
        response.update(complete=True, reviews=[
            dict(candidate_id=item["candidate_id"], start=item["start"] + 2,
                 end=item["end"] - 2, category="토크", status="검토 필요",
                 reason="대본을 읽었으며 움직임과 소리는 확인하지 못함",
                 needs_context=True, evidence="transcript", inspected_frames=[])
            for item in read(folder / "packet.json")["items"]
        ])
        return response

    def import_response(self, folder, response):
        path = folder / "response.json"
        save(path, response)
        with redirect_stdout(io.StringIO()):
            assistant.import_result(SimpleNamespace(job=self.job, input=path))

    def seed_prior_text(self):
        save(self.job / "text-candidates.json", [dict(reason="이전 결과 유지")])
        save(self.job / "text-analysis.json", dict(provider="previous"))
        return {name: (self.job / name).read_bytes()
                for name in ("text-candidates.json", "text-analysis.json")}

    def assert_prior_unchanged(self, snapshot):
        for name, content in snapshot.items():
            self.assertEqual((self.job / name).read_bytes(), content, name)

    def test_text_windows_cover_source_range_and_include_boundary_context(self):
        folder = self.prepare()
        packet = read(folder / "packet.json")
        windows = packet["windows"]
        self.assertEqual(packet["time_base"], "source_seconds")
        self.assertEqual([(w["core_start"], w["core_end"]) for w in windows],
                         [(100, 700), (700, 1300), (1300, 1350)])
        self.assertEqual([(w["start"], w["end"]) for w in windows],
                         [(100, 730), (670, 1330), (1270, 1350)])
        for timestamp in (100, 699.99, 700, 1299.99, 1300, 1349.99):
            self.assertEqual(sum(w["core_start"] <= timestamp < w["core_end"]
                                 for w in windows), 1)
        evidence = [read(folder / w["file"]) for w in windows]
        self.assertIn(self.words[2], evidence[0]["transcript"])
        self.assertIn(self.words[2], evidence[1]["transcript"])
        self.assertEqual(evidence[0]["chat_bins"], [dict(time=700, count=4)])
        self.assertEqual(evidence[1]["signal_candidates"], [dict(start=690, end=740)])
        self.assertNotIn(self.words[0], evidence[0]["transcript"])
        self.assertNotIn(self.words[-1], evidence[-1]["transcript"])
        self.assertTrue((folder / "INSTRUCTIONS.md").is_file())
        self.assertFalse((self.job / "text-candidates.json").exists())
        self.assertEqual(self.prepare(), folder)

    def test_unfinished_template_cannot_replace_existing_analysis(self):
        folder = self.prepare()
        prior = self.seed_prior_text()
        with self.assertRaisesRegex(ValueError, "분석 대기"):
            self.import_response(folder, read(folder / "response-template.json"))
        self.assert_prior_unchanged(prior)
        self.assertFalse((folder / "imported-response.json").exists())

    def test_response_schema_version_requires_integer_not_boolean(self):
        folder = self.prepare()
        response = self.text_response(folder)
        response["schema_version"] = True
        prior = self.seed_prior_text()
        with self.assertRaises(ValueError):
            self.import_response(folder, response)
        self.assert_prior_unchanged(prior)

    def test_text_requires_each_window_exactly_once_even_if_no_candidates(self):
        folder = self.prepare()
        good = self.text_response(folder)
        prior = self.seed_prior_text()
        for ids in ([], good["reviewed_windows"][:-1],
                    good["reviewed_windows"] + ["window-000"],
                    ["window-000", "window-001", "unknown"], None):
            with self.subTest(ids=ids):
                response = copy.deepcopy(good)
                response["reviewed_windows"] = ids
                with self.assertRaises(ValueError):
                    self.import_response(folder, response)
                self.assert_prior_unchanged(prior)

    def test_invalid_candidate_rejects_whole_response_before_writing(self):
        folder = self.prepare()
        prior = self.seed_prior_text()
        invalid = [
            self.candidate(window_id="unknown"), self.candidate(start=99),
            self.candidate(start=720, end=750), self.candidate(end=120),
            self.candidate(end=125), self.candidate(end=301),
            self.candidate(start=float("nan")), self.candidate(end=float("inf")),
            self.candidate(start=True), self.candidate(end=False),
            self.candidate(start="120"), self.candidate(category="unknown"),
            self.candidate(category=[]), self.candidate(reason="  "),
            self.candidate(reason=None), None,
        ]
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                # A valid first result must not be committed before a later invalid item.
                response = self.text_response(folder, [self.candidate(), candidate])
                with self.assertRaises(ValueError):
                    self.import_response(folder, response)
                self.assert_prior_unchanged(prior)
        with self.assertRaises(ValueError):
            self.import_response(folder, self.text_response(folder, [self.candidate()] * 6))
        self.assert_prior_unchanged(prior)

    def test_changed_job_transcript_or_signals_reject_stale_text_packet(self):
        for filename, replacement in (
            ("job.json", {**self.config, "chat_offset": 5}),
            ("job.json", {**self.config, "video_id": "different-source"}),
            ("transcript.json", self.words + [dict(start=120, end=125, text="수정")]),
            ("chat-bins.json", []),
            ("signal-candidates.json", []),
        ):
            with self.subTest(filename=filename, replacement=replacement):
                old = read(self.job / filename)
                folder = self.prepare()
                response = self.text_response(folder, [self.candidate()])
                prior = self.seed_prior_text()
                save(self.job / filename, replacement)
                with self.assertRaisesRegex(ValueError, "오래된 응답"):
                    self.import_response(folder, response)
                self.assert_prior_unchanged(prior)
                save(self.job / filename, old)

    def test_complete_empty_analysis_is_valid_and_explicitly_recorded(self):
        save(self.job / "transcript.json", [])
        folder = self.prepare()
        self.seed_prior_text()
        self.import_response(folder, self.text_response(folder))
        self.assertEqual(read(self.job / "text-candidates.json"), [])
        metadata = read(self.job / "text-analysis.json")
        self.assertTrue(metadata["complete"])
        self.assertEqual(metadata["provider"], "assistant")
        self.assertEqual(metadata["total_windows"], 3)
        self.assertEqual(len(metadata["reviewed_windows"]), 3)

    def test_text_import_is_idempotent_and_preserves_source_times(self):
        folder = self.prepare()
        response = self.text_response(folder, [self.candidate()])
        self.import_response(folder, response)
        first = {name: (self.job / name).read_bytes()
                 for name in ("text-candidates.json", "text-analysis.json")}
        self.import_response(folder, response)
        self.assert_prior_unchanged(first)
        result = read(self.job / "text-candidates.json")
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["start"], result[0]["end"]), (120, 150))
        self.assertEqual(result[0]["status"], "검토 필요")
        self.assertEqual(result[0]["sources"], ["transcript", "assistant"])

    def test_combine_rejects_stale_assistant_evidence_without_replacing_candidates(self):
        save(self.job / "signal-candidates.json", [])
        folder = self.prepare()
        self.import_response(folder, self.text_response(folder, [self.candidate()]))
        args = SimpleNamespace(job=self.job)
        with redirect_stdout(io.StringIO()):
            combine(args)
        combined = read(self.job / "candidates.json")
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["start"], 120)
        self.assertIn("assistant", combined[0]["sources"])
        prior = (self.job / "candidates.json").read_bytes()
        for filename, replacement in (
            ("transcript.json", []),
            ("job.json", {**self.config, "chat_offset": 10}),
        ):
            with self.subTest(filename=filename):
                old = read(self.job / filename)
                save(self.job / filename, replacement)
                with self.assertRaises(ValueError):
                    combine(args)
                self.assertEqual((self.job / "candidates.json").read_bytes(), prior)
                save(self.job / filename, old)

    def test_no_candidates_completes_text_combine_review_and_report_without_ffmpeg(self):
        save(self.job / "signal-candidates.json", [])
        text_folder = self.prepare()
        self.import_response(text_folder, self.text_response(text_folder))
        args = SimpleNamespace(job=self.job)
        with redirect_stdout(io.StringIO()):
            combine(args)
        self.assertEqual(read(self.job / "candidates.json"), [])
        with patch("chzzk_demo.assistant.shutil.which", return_value=None), \
                patch("chzzk_demo.assistant._frame", side_effect=AssertionError("no frames")):
            review_folder = self.prepare("review", frames=6)
        self.assertEqual(read(review_folder / "packet.json")["items"], [])
        self.import_response(review_folder, self.review_response(review_folder))
        with redirect_stdout(io.StringIO()):
            report(args)
        self.assertTrue((self.job / "report.html").is_file())

    def test_windows_editor_utf8_bom_response_is_accepted(self):
        folder = self.prepare()
        path = folder / "response.json"
        path.write_text(json.dumps(self.text_response(folder)), encoding="utf-8-sig")
        with redirect_stdout(io.StringIO()):
            assistant.import_result(SimpleNamespace(job=self.job, input=path))
        self.assertEqual(read(self.job / "text-candidates.json"), [])

    def test_text_only_review_needs_neither_ffmpeg_nor_clip(self):
        with patch("chzzk_demo.assistant.shutil.which", return_value=None), \
                patch("chzzk_demo.assistant._frame", side_effect=AssertionError("no video decoding")):
            folder = self.prepare("review", frames=0)
        packet = read(folder / "packet.json")
        self.assertEqual(len(packet["items"]), 2)
        self.assertEqual(packet["skipped_candidates"], [])
        for item in packet["items"]:
            self.assertEqual(item["frames"], [])
            self.assertIsNone(item["clip"])
        first = read(folder / packet["items"][0]["file"])
        self.assertEqual(first["chat"], [dict(time=111, text="보정 후 클립 안")])
        self.assertEqual(first["candidate"], self.candidates[0])

    def test_review_import_converts_source_to_clip_time_without_human_approval(self):
        folder = self.prepare("review")
        response = self.review_response(folder)
        response["reviews"][0].update(start=125, end=160)
        self.import_response(folder, response)
        path = self.job / "reviews" / "first-assistant.json"
        result = read(path)
        self.assertEqual((result["start"], result["end"]), (15, 50))
        self.assertEqual((result["source_start"], result["source_end"]), (125, 160))
        self.assertEqual(result["provider"], "assistant")
        self.assertFalse(result["human_approved"])
        self.assertEqual(result["evidence"], "transcript")
        self.assertEqual(result["inspected_frames"], [])
        first = path.read_bytes()
        self.import_response(folder, response)
        self.assertEqual(path.read_bytes(), first)
        self.assertFalse((self.job / "text-candidates.json").exists())

    def test_invalid_review_preserves_all_previous_results(self):
        folder = self.prepare("review")
        good = self.review_response(folder)
        self.import_response(folder, good)
        prior = {f"reviews/{cid}-assistant.json":
                 (self.job / "reviews" / f"{cid}-assistant.json").read_bytes()
                 for cid in ("first", "second")}
        changes = [
            dict(candidate_id="unknown"), dict(start=2, end=30),
            dict(start=100), dict(end=171), dict(start=float("nan")),
            dict(start=True), dict(needs_context="true"),
            dict(status="승인"), dict(status="편집 우선", needs_context=True),
            dict(evidence="video"), dict(evidence="sampled_frames"),
            dict(inspected_frames=["frames/first-00.jpg"]),
        ]
        for change in changes:
            with self.subTest(change=change):
                response = copy.deepcopy(good)
                response["reviews"][0].update(change)
                response["reviews"][1]["reason"] = "저장되면 안 되는 새 결과"
                with self.assertRaises(ValueError):
                    self.import_response(folder, response)
                self.assert_prior_unchanged(prior)
        for reviews in (good["reviews"][:1], good["reviews"] * 2, [None], None):
            with self.subTest(reviews=reviews):
                response = copy.deepcopy(good)
                response["reviews"] = reviews
                with self.assertRaises(ValueError):
                    self.import_response(folder, response)
                self.assert_prior_unchanged(prior)

    def test_changed_candidates_chat_or_clip_reject_stale_review(self):
        clip = self.job / "clips" / "first.mp4"
        clip.parent.mkdir()
        clip.write_bytes(b"original fake clip")
        for kind in ("candidates", "chat", "clip"):
            with self.subTest(kind=kind):
                folder = self.prepare("review")
                response = self.review_response(folder)
                self.import_response(folder, response)
                path = self.job / "reviews" / "first-assistant.json"
                prior = path.read_bytes()
                if kind == "candidates":
                    changed = copy.deepcopy(self.candidates)
                    changed[0]["start"] += 1
                    save(self.job / "candidates.json", changed)
                elif kind == "chat":
                    save(self.job / "chats.json", [])
                else:
                    clip.write_bytes(b"replaced clip, different bytes and size")
                with self.assertRaisesRegex(ValueError, "오래된 응답"):
                    self.import_response(folder, response)
                self.assertEqual(path.read_bytes(), prior)
                save(self.job / "candidates.json", self.candidates)

    def test_sampled_frames_are_declared_and_missing_clips_are_skipped(self):
        clip = self.job / "clips" / "first.mp4"
        clip.parent.mkdir()
        clip.write_bytes(b"fake clip")

        def fake_frame(video, output, seconds):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"fake JPEG for unit test")

        with patch("chzzk_demo.assistant.shutil.which", return_value="ffmpeg"), \
                patch("chzzk_demo.assistant._frame", side_effect=fake_frame) as extract:
            folder = self.prepare("review", frames=6)
            self.assertEqual(extract.call_count, 6)
            self.assertEqual([call.args[2] for call in extract.call_args_list],
                             [5, 15, 25, 35, 45, 55])
            # Re-preparing identical source inputs reuses already extracted images.
            self.assertEqual(self.prepare("review", frames=6), folder)
            self.assertEqual(extract.call_count, 6)
        packet = read(folder / "packet.json")
        self.assertEqual(packet["skipped_candidates"], ["second"])
        self.assertEqual(len(packet["items"]), 1)
        item = packet["items"][0]
        self.assertEqual([f["source_time"] for f in item["frames"]],
                         [115, 125, 135, 145, 155, 165])
        self.assertTrue(all((folder / f["path"]).is_file() for f in item["frames"]))
        response = self.review_response(folder)
        inspected = [item["frames"][0]["path"], item["frames"][-1]["path"]]
        response["reviews"][0].update(evidence="sampled_frames", inspected_frames=inspected)
        self.import_response(folder, response)
        result = read(self.job / "reviews" / "first-assistant.json")
        self.assertEqual(result["evidence"], "sampled_frames")
        self.assertEqual(result["inspected_frames"], inspected)
        self.assertFalse((self.job / "reviews" / "second-assistant.json").exists())
        for frames, evidence in ((["frames/second-00.jpg"], "sampled_frames"),
                                 (inspected * 2, "sampled_frames"),
                                 (inspected, "transcript")):
            with self.subTest(frames=frames, evidence=evidence):
                response["reviews"][0].update(inspected_frames=frames, evidence=evidence)
                with self.assertRaises(ValueError):
                    self.import_response(folder, response)
                self.assertEqual(read(self.job / "reviews" / "first-assistant.json"), result)

    def test_frame_review_requires_ffmpeg_and_at_least_one_existing_clip(self):
        with patch("chzzk_demo.assistant.shutil.which", return_value="ffmpeg"):
            with self.assertRaisesRegex(ValueError, "클립"):
                self.prepare("review", frames=6)
        clip = self.job / "clips" / "first.mp4"
        clip.parent.mkdir()
        clip.write_bytes(b"fake clip")
        with patch("chzzk_demo.assistant.shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "FFmpeg"):
                self.prepare("review", frames=6)

    def test_cli_prepares_and_imports_offline_response(self):
        stdout = io.StringIO()
        with patch("sys.argv", ["chzzk-demo", "assistant-prepare", "--job", str(self.job)]), \
                redirect_stdout(stdout), \
                patch("chzzk_demo.assistant._frame", side_effect=AssertionError("no ffmpeg")):
            main()
        self.assertIn("분석 대기", stdout.getvalue())
        folder = next((self.job / "assistant" / "text").iterdir())
        path = folder / "response.json"
        save(path, self.text_response(folder, [self.candidate()]))
        with patch("sys.argv", ["chzzk-demo", "assistant-import", "--job", str(self.job),
                                "--input", str(path)]), redirect_stdout(io.StringIO()):
            main()
        self.assertEqual(len(read(self.job / "text-candidates.json")), 1)
        with patch("sys.argv", ["chzzk-demo", "assistant-prepare", "--job", str(self.job),
                                "--frames", "25"]), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                main()
        self.assertEqual(failure.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
