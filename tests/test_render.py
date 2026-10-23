import importlib.util
import math
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chzzk_demo.core import read, save
from chzzk_demo.render import ass_text, render_plan, transform_captions, validate_plan


def plan():
    return dict(slug="example", source_start=100, title_lines=["제목", "마지막 반응"],
                channel="채널", font_name="Malgun Gothic", keep=[[100.1, 100.7], [102.1, 102.7]],
                captions=[dict(start=100.2, end=100.6, text="첫 장면"),
                          dict(start=102.2, end=102.6, text="마지막 장면", style="Punchline")])


class EditPlanTests(unittest.TestCase):
    def validate(self, value):
        return validate_plan(value, source_duration=3, source_width=1920, source_height=1080)

    def test_offsets_and_caption_times_preserve_chronology(self):
        value = self.validate(plan())
        cues = transform_captions(value)
        self.assertAlmostEqual(value["duration_seconds"], 1.2)
        self.assertEqual(value["crop"], dict(x=240, y=0, width=1440, height=1080))
        self.assertAlmostEqual(cues[0]["start"], .1)
        self.assertAlmostEqual(cues[1]["start"], .7)
        self.assertAlmostEqual(cues[1]["end"], 1.1)
        self.assertEqual(cues[1]["source_start"], 102.2)

    def test_rejects_invalid_source_intervals(self):
        cases = [[], [[99, 100.5]], [[100, 103.1]], [[101, 102], [100, 101]],
                 [[100, 102], [101, 103]], [[100, 100]], [[100, math.inf]],
                 [[100, math.nan]], [[True, 102]], [[100, 100.01]]]
        for keep in cases:
            with self.subTest(keep=keep), self.assertRaises(ValueError):
                self.validate({**plan(), "keep": keep, "captions": []})

    def test_rejects_caption_spanning_removed_time_or_other_caption(self):
        for captions in [
            [dict(start=100.5, end=102.5, text="이어지는 말")],
            [dict(start=100.1, end=100.5, text="말"), dict(start=100.4, end=100.6, text="겹침")],
            [dict(start=102.2, end=102.4, text="뒤"), dict(start=100.2, end=100.4, text="앞")],
            [dict(start=100.1, end=100.5, text="말", style="Unknown")],
        ]:
            with self.subTest(captions=captions), self.assertRaises(ValueError):
                self.validate({**plan(), "captions": captions})

    def test_rejects_path_traversal_crop_and_style_injection(self):
        for changes in [
            dict(slug="../other"), dict(slug="manifest"), dict(font_name="font\nStyle: surprise"),
            dict(font_name="font,100"), dict(title_lines=["one", "two", "three"]),
            dict(crop=dict(x=482, y=0, width=1440, height=1080)),
            dict(crop=dict(x=0, y=0, width=1920, height=1080)),
            dict(context=[dict(start=0, end=2, text="범위 밖")]),
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate({**plan(), **changes})

    def test_caption_override_syntax_is_literal_not_executable(self):
        text = "{\\pos(0,0)}한글\\N문자\n둘째 줄"
        escaped = ass_text(text)
        self.assertNotIn("{", escaped)
        self.assertNotIn(r"\pos", escaped)
        self.assertEqual(escaped.count(r"\N"), 1)
        self.assertIn("｛＼pos(0,0)｝", escaped)


FONT = next((Path(p) for p in ["C:/Windows/Fonts/malgunbd.ttf",
                              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"] if Path(p).is_file()), None)
HAS_RENDER = bool(shutil.which("ffmpeg") and shutil.which("ffprobe") and FONT and importlib.util.find_spec("PIL"))


@unittest.skipUnless(HAS_RENDER, "FFmpeg, ffprobe, Pillow, and a TrueType font are needed for synthetic render")
class SyntheticRenderTests(unittest.TestCase):
    def test_source_offset_order_pixels_subtitles_upsert_and_failure_preservation(self):
        from PIL import Image, ImageFont

        with tempfile.TemporaryDirectory(prefix="shorts test ") as temporary:
            root = Path(temporary)
            source, logo = root / "source.mp4", root / "logo.png"
            Image.new("RGB", (16, 16), (0, 255, 100)).save(logo)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "color=red:s=320x240:r=30:d=1",
                "-f", "lavfi", "-i", "color=green:s=320x240:r=30:d=1",
                "-f", "lavfi", "-i", "color=blue:s=320x240:r=30:d=1",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(source),
            ], check=True, capture_output=True)
            value = {**plan(), "source": source.name, "logo": logo.name, "font": str(FONT),
                     "font_name": ImageFont.truetype(str(FONT)).getname()[0]}
            path, output = root / "edit.json", root / "out"
            save(path, value)
            row = render_plan(path, output, gallery=True)
            self.assertAlmostEqual(row["actual_duration_seconds"], 1.2, delta=.1)
            self.assertTrue(row["source_has_audio"])
            self.assertFalse(row["human_approved"])
            self.assertEqual(row["layout"]["panel"], [0, 555, 1080, 810])

            def pixel(time, y=900):
                result = subprocess.run([
                    "ffmpeg", "-v", "error", "-ss", str(time), "-i", str(output / "example.mp4"),
                    "-frames:v", "1", "-vf", f"crop=4:4:538:{y},format=rgb24",
                    "-f", "rawvideo", "pipe:1",
                ], check=True, capture_output=True)
                return tuple(result.stdout[:3])

            red, blue, black = pixel(.2), pixel(.9), pixel(.2, 100)
            self.assertGreater(red[0], 220)
            self.assertLess(red[2], 30)
            self.assertGreater(blue[2], 220)
            self.assertLess(blue[0], 30)
            self.assertLess(max(black), 10)
            srt = (output / "example.ko.srt").read_text(encoding="utf-8-sig")
            self.assertIn("00:00:00,700 --> 00:00:01,100", srt)
            self.assertIn("마지막 장면", srt)
            self.assertIn('src="example.mp4"', (output / "index.html").read_text(encoding="utf-8"))
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(output / "example.mp4"),
                            "-f", "null", os.devnull], check=True, capture_output=True)

            # Same-slug edits replace one manifest row, instead of duplicating it.
            save(path, {**value, "title_lines": ["수정한 제목"]})
            render_plan(path, output, gallery=True)
            manifest = read(output / "manifest.json")
            self.assertEqual(len(manifest["renders"]), 1)
            self.assertEqual(manifest["renders"][0]["title_lines"], ["수정한 제목"])
            before = {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
            original_run = subprocess.run

            def fail_encode(args, **kwargs):
                if args[0] == "ffmpeg":
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="synthetic encoder failure")
                return original_run(args, **kwargs)

            with patch("chzzk_demo.render.subprocess.run", side_effect=fail_encode), \
                    self.assertRaisesRegex(RuntimeError, "이전 출력 보존"):
                render_plan(path, output, gallery=True)
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()})
            self.assertFalse(any(p.name.startswith(".render-") for p in output.iterdir()))

            # The source can never be replaced by a matching output slug.
            save(path, {**value, "slug": "source"})
            with self.assertRaisesRegex(ValueError, "입력 파일과 같습니다"):
                render_plan(path, root)


if __name__ == "__main__":
    unittest.main()
