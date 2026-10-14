import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from chzzk_demo.core import (chat_bins, chunks, merge_candidates, normalize_chat,
                             read, save, valid_ai_candidate)
from chzzk_demo.ingest import chats
from chzzk_demo.report import report
from chzzk_demo.analyze import signals, combine


class PipelineTests(unittest.TestCase):
    def test_local_signal_pipeline_without_network_gpu(self):
        import wave
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            save(job/'job.json', dict(start=100, end=160, video='not-used.mp4'))
            samples = np.zeros(60*16000, dtype='<i2')
            samples[30*16000:31*16000] = 20000
            with wave.open(str(job/'analysis.wav'), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(samples.tobytes())
            args = SimpleNamespace(job=folder, allow_no_chat=True)
            signals(args)
            combine(args)
            result = read(job/'candidates.json')
            self.assertTrue(result)
            self.assertIn('audio', result[0]['sources'])
            self.assertGreaterEqual(result[0]['start'], 100)
            self.assertLessEqual(result[0]['end'], 160)
            report(args)
            self.assertTrue((job/'report.html').exists())

    def test_chunk_midpoint_ownership_no_gap_no_double_count(self):
        windows = list(chunks(100, 7300))
        self.assertEqual(len(windows), 6)
        for time in [100, 1299.99, 1300, 1300.01, 7299.99]:
            self.assertEqual(sum(w['core_start'] <= time < w['core_end'] for w in windows), 1)
        self.assertEqual(windows[1]['start'], 1295)

    def test_vod_time_not_wall_clock(self):
        raw = {'playerMessageTime': 7542300, 'messageTime': 9999999999999, 'content': 'hello'}
        self.assertAlmostEqual(normalize_chat(raw, -2)['time'], 7540.3)
        self.assertIsNone(normalize_chat({'messageTime': 999}))

    def test_chat_spam_dedup_and_no_chat_is_not_highlight_verdict(self):
        messages = [{'time': 15, 'user': 'u', 'text': 'ㅋㅋ'} for _ in range(100)]
        bins = chat_bins(messages, 0, 30)
        self.assertEqual(bins[1]['count'], 1)
        self.assertEqual(bins[0]['count'], 0)
        self.assertNotIn('rejected', bins[0])

    def test_merge_preserves_independent_signal(self):
        def item(a, b, source):
            return dict(start=a, end=b, sources=[source], reason='test', priority=1, category='미분류')
        result = merge_candidates([item(0, 75, 'chat'), item(10, 85, 'audio'), item(400, 475, 'transcript')])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['sources'], ['audio', 'chat'])
        self.assertEqual(result[1]['sources'], ['transcript'])

    def test_ai_times_must_be_finite_and_in_bounds(self):
        for a, b in [(float('nan'), 30), (-1, 30), (20, 10), (10, 1000)]:
            self.assertFalse(valid_ai_candidate(dict(start=a, end=b, category='게임'), 0, 60))
        self.assertTrue(valid_ai_candidate(dict(start=1, end=30, category='게임'), 0, 60))

    def test_chat_resume_empty_page_with_next_cursor(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            save(job/'job.json', dict(video_id='123', start=0, end=2))
            save(job/'chat-pages/0.json', {'content': {'videoChats': [], 'nextPlayerMessageTime': 1000}})
            message = dict(playerMessageTime=1500, messageTime=9999, content='ok', userIdHash='a')
            save(job/'chat-pages/1000.json', {'content': {'videoChats': [message, message], 'nextPlayerMessageTime': None}})
            with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network should not be used')):
                chats(SimpleNamespace(job=folder, cookies=None))
            self.assertEqual(len(read(job/'chats.json')), 1)
            self.assertEqual(read(job/'chat-status.json')['status'], 'fetched_unverified')

    def test_nonadvancing_chat_cursor_fails_instead_of_claiming_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            save(job/'job.json', dict(video_id='123', start=0, end=2))
            save(job/'chat-pages/0.json', {'content': {'videoChats': [], 'nextPlayerMessageTime': 0}})
            with self.assertRaises(ValueError):
                chats(SimpleNamespace(job=folder, cookies=None))
            self.assertEqual(read(job/'chat-status.json')['status'], 'failed')

    def test_report_does_not_execute_chat_html(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            save(job/'job.json', dict(start=0, end=60))
            save(job/'candidates.json', [dict(id='test', start=0, end=30, sources=['chat'],
                reason='</script><script>alert(1)</script>', category='미분류')])
            save(job/'chats.json', [dict(time=1, text='<img onerror=alert(1)>')])
            report(SimpleNamespace(job=folder))
            text = (job/'report.html').read_text(encoding='utf-8')
            self.assertNotIn('</script><script>alert(1)', text)
            self.assertIn('\\u003c', text)


if __name__ == '__main__':
    unittest.main()
