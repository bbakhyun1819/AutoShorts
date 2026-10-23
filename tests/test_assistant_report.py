import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chzzk_demo.core import fingerprint, save
from chzzk_demo.report import report


class AssistantReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.job = Path(self.temporary.name)
        save(self.job / "job.json", {"start": 0, "end": 120})
        self.current_fingerprint = Mock(side_effect=lambda job, stage: f"{stage}-current")
        module = SimpleNamespace(input_fingerprint=self.current_fingerprint)
        modules = patch.dict("sys.modules", {"chzzk_demo.assistant": module})
        modules.start()
        self.addCleanup(modules.stop)

    def candidate(self, identifier):
        return {"id": identifier, "start": 10, "end": 40, "category": "토크",
                "sources": ["transcript", "assistant"], "reason": "대본 근거", "priority": 4}

    def review(self, candidate, evidence="transcript", **changes):
        value = {"candidate_id": candidate["id"], "provider": "assistant",
                 "evidence": evidence, "candidate_signature": fingerprint(candidate),
                 "input_fingerprint": "review-current", "packet_id": "packet",
                 "status": "검토 필요", "reason": "직접 검토 근거",
                 "source_start": 12, "source_end": 35, "human_approved": False}
        value.update(changes)
        return value

    def text_metadata(self, candidates, **changes):
        value = {"provider": "assistant", "input_fingerprint": "text-current",
                 "candidates_fingerprint": fingerprint(candidates), "packet_id": "packet",
                 "reviewed_windows": ["window-0", "window-1"], "total_windows": 2, "complete": True}
        value.update(changes)
        save(self.job / "text-candidates.json", candidates)
        save(self.job / "text-analysis.json", value)

    def render(self):
        report(SimpleNamespace(job=str(self.job)))
        page = (self.job / "report.html").read_text(encoding="utf-8")
        payload = re.search(r"const data=(.*?); const root=", page).group(1)
        return page, json.loads(payload)

    def test_provenance_and_counts_only_current_linked_reviews(self):
        items = [self.candidate(name) for name in ("transcript", "frames", "gemini")]
        save(self.job / "candidates.json", items)
        save(self.job / "reviews/transcript.json", self.review(items[0]))
        save(self.job / "reviews/frames.json", self.review(items[1], evidence="sampled_frames"))
        legacy = {"candidate_id": "gemini", "model": "gemini-test", "status": "편집 우선",
                  "reason": "영상 근거", "source_start": 10, "source_end": 40}
        save(self.job / "reviews/gemini.json", legacy)
        save(self.job / "reviews/orphan.json", dict(legacy, candidate_id="removed"))
        page, rows = self.render()
        self.assertIn("Codex 대본 검토 1개", page)
        self.assertIn("Codex 표본 프레임 검토 1개", page)
        self.assertIn("Gemini 영상 검토 1개", page)
        self.assertEqual(rows[0]["review_label"], "Codex 대본 검토 (영상·음성 미검토)")
        self.assertEqual(rows[1]["review_label"], "Codex 표본 프레임 검토 (전체 영상·음성 미검토)")
        self.assertEqual(rows[2]["review_label"], "Gemini 영상 검토 (gemini-test)")
        self.current_fingerprint.assert_called_once_with(self.job, "review")

    def test_stale_newest_review_does_not_hide_applicable_older_review(self):
        candidate = self.candidate("one")
        save(self.job / "candidates.json", [candidate])
        older = self.job / "reviews/older.json"
        newest = self.job / "reviews/newest.json"
        save(older, self.review(candidate, reason="현재 입력의 검토"))
        save(newest, self.review(candidate, input_fingerprint="old-input", reason="이전 입력의 검토"))
        os.utime(older, (1000, 1000))
        os.utime(newest, (2000, 2000))
        _, rows = self.render()
        self.assertEqual(rows[0]["review"]["reason"], "현재 입력의 검토")
        self.current_fingerprint.assert_called_once_with(self.job, "review")

    def test_stale_candidate_and_evidence_reviews_are_not_counted(self):
        first, second = self.candidate("one"), self.candidate("two")
        save(self.job / "reviews/one.json", self.review(first))
        save(self.job / "reviews/two.json", self.review(second, input_fingerprint="old-input"))
        first["reason"] = "같은 ID의 수정된 후보"
        save(self.job / "candidates.json", [first, second])
        page, rows = self.render()
        self.assertTrue(all(row["review"] is None for row in rows))
        self.assertIn("Codex 대본 검토 0개", page)
        self.assertIn("Gemini 영상 검토 0개", page)

    def test_text_analysis_requires_complete_current_matching_metadata(self):
        items = [self.candidate("one")]
        self.text_metadata(items)
        self.assertIn("Codex 대본 의미 분석 완료 (2/2구간)", self.render()[0])
        self.text_metadata(items, complete=False, reviewed_windows=["window-0"])
        self.assertIn("Codex 대본 의미 분석 일부 완료 (1/2구간)", self.render()[0])
        self.text_metadata(items, input_fingerprint="old-input")
        self.assertIn("Codex 대본 분석 결과 오래됨", self.render()[0])
        self.text_metadata(items, candidates_fingerprint="different-candidates")
        self.assertIn("Codex 대본 분석 상태 확인 필요", self.render()[0])

    def test_empty_assistant_analysis_can_be_complete_without_candidates(self):
        self.text_metadata([])
        self.assertIn("Codex 대본 의미 분석 완료 (2/2구간)", self.render()[0])

    def test_gemini_overwrite_does_not_reuse_assistant_completion(self):
        items = [self.candidate("one")]
        self.text_metadata(items)
        items[0]["sources"] = ["transcript"]
        save(self.job / "text-candidates.json", items)
        page, _ = self.render()
        self.assertIn("대본 의미 분석 완료 (Gemini)", page)
        self.assertNotIn("Codex 대본 의미 분석 완료", page)
        # A later Gemini run may also return no candidates; its provider remains explicit.
        save(self.job / "text-candidates.json", [])
        save(self.job / "text-analysis.json", {"provider": "gemini"})
        self.assertIn("대본 의미 분석 완료 (Gemini)", self.render()[0])
        self.current_fingerprint.assert_not_called()

    def test_review_reason_and_model_are_escaped_and_rendered_as_text(self):
        candidate = self.candidate("one")
        save(self.job / "candidates.json", [candidate])
        malicious = "</script><script>alert('review')</script>&"
        legacy = {"candidate_id": "one", "model": malicious, "status": "검토 필요",
                  "reason": malicious, "source_start": 10, "source_end": 40}
        save(self.job / "reviews/gemini.json", legacy)
        save(self.job / "job.json", {"start": 0, "end": 120, "sync_status": malicious})
        page, rows = self.render()
        self.assertNotIn(malicious, page)
        self.assertIn("\\u003c/script>", page)
        self.assertIn("&lt;/script&gt;", page)
        self.assertIn("e.textContent=text", page)
        self.assertNotIn("innerHTML", page)
        self.assertEqual(rows[0]["review"]["reason"], malicious)


if __name__ == "__main__":
    unittest.main()
