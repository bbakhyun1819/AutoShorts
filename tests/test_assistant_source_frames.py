import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from chzzk_demo import assistant
from chzzk_demo.cli import main
from chzzk_demo.core import read, save
from chzzk_demo.report import report


class AssistantSourceFrameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.job = Path(temporary.name)
        self.source = self.job / "source.mp4"
        self.source.write_bytes(b"original source stand-in")
        self.config = dict(video=str(self.source), start=2880, end=17400,
                           source_duration=20000, chat_offset=0, sync_status="unverified")
        self.candidate = dict(id="first", start=3000, end=3060, category="토크",
                              sources=["transcript", "assistant"], reason="대화의 반전", priority=4)
        save(self.job / "job.json", self.config)
        save(self.job / "transcript.json", [dict(start=3001, end=3005, text="반전 대사")])
        save(self.job / "candidates.json", [self.candidate])

    @staticmethod
    def fake_frame(video, output, seconds):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"frame stand-in")

    def prepare(self, stage="review", **changes):
        args = dict(job=self.job, stage=stage, limit=10, frames=2, from_source=True)
        args.update(changes)
        with redirect_stdout(io.StringIO()):
            return assistant.prepare(SimpleNamespace(**args))

    def test_source_frames_use_absolute_times_without_creating_clips(self):
        with patch("chzzk_demo.assistant.shutil.which", return_value="ffmpeg"), \
                patch("chzzk_demo.assistant._frame", side_effect=self.fake_frame) as extract:
            folder = self.prepare()
            self.assertEqual([call.args[0] for call in extract.call_args_list], [self.source] * 2)
            self.assertEqual([call.args[2] for call in extract.call_args_list], [3015, 3045])
            self.assertEqual(self.prepare(), folder)
            self.assertEqual(extract.call_count, 2)
        packet = read(folder / "packet.json")
        self.assertTrue(packet["options"]["from_source"])
        self.assertEqual(packet["skipped_candidates"], [])
        item = packet["items"][0]
        self.assertIsNone(item["clip"])
        self.assertEqual([frame["clip_time"] for frame in item["frames"]], [15, 45])
        self.assertEqual([frame["source_time"] for frame in item["frames"]], [3015, 3045])
        self.assertFalse((self.job / "clips").exists())
        self.assertIsNone(read(folder / item["file"])["clip"])

    def test_source_flag_prefers_original_even_when_candidate_clip_exists(self):
        clip = self.job / "clips" / "first.mp4"
        clip.parent.mkdir()
        clip.write_bytes(b"cached candidate clip")
        with patch("chzzk_demo.assistant.shutil.which", return_value="ffmpeg"), \
                patch("chzzk_demo.assistant._frame", side_effect=self.fake_frame) as extract:
            from_source = self.prepare()
            self.assertEqual([call.args[0] for call in extract.call_args_list], [self.source] * 2)
            extract.reset_mock()
            from_clip = self.prepare(from_source=False)
            self.assertEqual([call.args[0] for call in extract.call_args_list], [clip] * 2)
            self.assertEqual([call.args[2] for call in extract.call_args_list], [15, 45])
        self.assertNotEqual(from_source, from_clip)
        self.assertEqual(read(from_source / "packet.json")["items"][0]["clip"], str(clip))
        self.assertFalse(read(from_clip / "packet.json")["options"]["from_source"])

    def test_missing_source_has_useful_error_and_text_only_review_still_works(self):
        save(self.job / "job.json", {**self.config, "video": str(self.job / "missing.mp4")})
        with patch("chzzk_demo.assistant._frame") as extract:
            with self.assertRaisesRegex(ValueError, "원본 영상.*video"):
                self.prepare()
            folder = self.prepare(frames=0)
            extract.assert_not_called()
        self.assertEqual(read(folder / "packet.json")["items"][0]["frames"], [])

    def test_source_flag_does_not_change_text_packet_or_options(self):
        from_source = self.prepare(stage="text")
        ordinary = self.prepare(stage="text", from_source=False)
        self.assertEqual(from_source, ordinary)
        self.assertEqual(read(from_source / "packet.json")["options"], {})

    def test_explicit_candidate_selection_does_not_extract_other_candidates(self):
        second = {**self.candidate, "id": "second", "start": 3100, "end": 3160}
        save(self.job / "candidates.json", [self.candidate, second])
        with patch("chzzk_demo.assistant.shutil.which", return_value="ffmpeg"), \
                patch("chzzk_demo.assistant._frame", side_effect=self.fake_frame) as extract:
            folder = self.prepare(candidate_id=["second"], limit=1)
        packet = read(folder / "packet.json")
        self.assertEqual([x["candidate_id"] for x in packet["items"]], ["second"])
        self.assertEqual([c.args[2] for c in extract.call_args_list], [3115, 3145])
        self.assertEqual(packet["options"]["candidate_ids"], ["second"])
        with patch("chzzk_demo.assistant._frame") as extract:
            with self.assertRaisesRegex(ValueError, "없는 후보 ID"):
                self.prepare(candidate_id=["missing"])
            extract.assert_not_called()

    def test_source_frame_review_can_be_imported_and_reported_without_clip(self):
        with patch("chzzk_demo.assistant.shutil.which", return_value="ffmpeg"), \
                patch("chzzk_demo.assistant._frame", side_effect=self.fake_frame):
            folder = self.prepare()
        packet = read(folder / "packet.json")
        response = read(folder / "response-template.json")
        inspected = [frame["path"] for frame in packet["items"][0]["frames"]]
        response.update(complete=True, reviews=[dict(
            candidate_id="first", start=3001, end=3059, category="토크", status="편집 우선",
            reason="대본과 표본 화면 확인; 전체 영상과 음성은 미검토", needs_context=False,
            evidence="sampled_frames", inspected_frames=inspected,
        )])
        response_path = folder / "response.json"
        save(response_path, response)
        with redirect_stdout(io.StringIO()):
            assistant.import_result(SimpleNamespace(job=self.job, input=response_path))
            report(SimpleNamespace(job=self.job))
        review = read(self.job / "reviews" / "first-assistant.json")
        self.assertEqual(review["inspected_frames"], inspected)
        self.assertEqual((review["start"], review["end"]), (1, 59))
        self.assertEqual((review["source_start"], review["source_end"]), (3001, 3059))
        self.assertFalse(review["human_approved"])
        page = (self.job / "report.html").read_text(encoding="utf-8")
        rows = json.loads(re.search(r"const data=(.*?); const root=", page).group(1))
        self.assertEqual(rows[0]["review"]["evidence"], "sampled_frames")
        self.assertIn("Codex 표본 프레임 검토 1개", page)

    def test_cli_forwards_from_source_to_review_preparation(self):
        argv = ["chzzk-demo", "assistant-prepare", "--job", str(self.job),
                "--stage", "review", "--from-source"]
        with patch("sys.argv", argv), patch("chzzk_demo.assistant.prepare") as prepare:
            main()
        self.assertEqual(prepare.call_args.args[0].stage, "review")
        self.assertTrue(prepare.call_args.args[0].from_source)


if __name__ == "__main__":
    unittest.main()
