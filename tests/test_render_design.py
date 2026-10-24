import importlib.util
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chzzk_demo.core import save
from chzzk_demo.render import (
    DESIGN_DEFAULTS, _subtitles, normalize_design, preview_layout,
    render_plan, render_preview, transform_captions, validate_plan,
)


FONT = next((Path(p) for p in ["C:/Windows/Fonts/malgunbd.ttf",
                              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"] if Path(p).is_file()), None)
HAS_PIL = bool(FONT and importlib.util.find_spec("PIL"))
HAS_RENDER = bool(HAS_PIL and shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_plan():
    return dict(slug="design", source_start=100, title_lines=["첫 제목", "둘째 제목"],
                channel="쪄니", font_name="Malgun Gothic", keep=[[100.1, 100.7], [102.1, 102.7]],
                captions=[dict(start=100.2, end=100.6, text="처음 자막"),
                          dict(start=102.2, end=102.6, text="끝 자막", style="Punchline")])


def normalized(value=None):
    return validate_plan(value or make_plan(), source_duration=3, source_width=320, source_height=240)


class DesignValidationTests(unittest.TestCase):
    def test_missing_design_keeps_approved_defaults_and_partial_overrides_are_independent(self):
        self.assertEqual(normalized()["design"], DESIGN_DEFAULTS)
        result = normalized({**make_plan(), "design": {"title": {"size": 96}, "logo": {"x": 10}}})
        self.assertEqual(result["design"]["title"], {"x": 540, "y": 439, "size": 96, "line_gap": 88})
        self.assertEqual(result["design"]["logo"], {"x": 10, "y": 1572, "size": 156})
        result["design"]["caption"]["x"] = 1
        self.assertEqual(DESIGN_DEFAULTS["caption"]["x"], 540)

    def test_invalid_layout_is_rejected_before_rendering(self):
        for design in [[], {"unknown": {}}, {"title": {"foo": 10}}, {"title": []},
                       {"caption": {"x": math.nan}}, {"title": {"size": True}},
                       {"title": {"size": 0}}, {"title": {"size": 80.5}},
                       {"caption": {"y": 1921}}, {"logo": {"x": 1000}},
                       {"logo": {"size": 0}}, {"channel": {"x": -1}},
                       {"caption": {"emphasis_size": 241}}]:
            with self.subTest(design=design), self.assertRaises(ValueError):
                normalized({**make_plan(), "design": design})

    @unittest.skipUnless(HAS_PIL, "Pillow and a TrueType font are required")
    def test_default_and_overridden_layout_share_ass_and_preview_geometry(self):
        from PIL import ImageFont
        value = normalized()
        ass, _ = _subtitles(value, transform_captions(value), FONT)
        self.assertIn(r"\pos(540,395)\fs76", ass)
        self.assertIn(r"\pos(540,483)\fs76", ass)
        self.assertIn(r"\pos(540,1435)\fs58", ass)
        self.assertIn(r"\pos(540,1435)\fs76", ass)
        width = ImageFont.truetype(str(FONT), 92).getlength("쪄니")
        self.assertIn(fr"\pos({round(521 + width / 2)},1650)\fs92", ass)
        explicit = normalized({**make_plan(), "design": normalize_design()})
        self.assertEqual(_subtitles(explicit, transform_captions(explicit), FONT)[0], ass)
        value = normalized({**make_plan(), "design": {
            "title": {"x": 470, "y": 300, "size": 80, "line_gap": 100},
            "caption": {"x": 520, "y": 1500, "size": 40, "punchline_size": 72},
            "channel": {"x": 600, "y": 1770, "size": 64},
        }})
        ass, _ = _subtitles(value, transform_captions(value), FONT)
        self.assertIn(r"\pos(470,250)\fs80", ass)
        self.assertIn(r"\pos(470,350)\fs80", ass)
        self.assertIn(r"\pos(520,1500)\fs72", ass)
        self.assertIn(r"\pos(600,1770)\fs64", ass)
        layout = preview_layout(value, FONT, .9)
        self.assertEqual([(p["text"], p["size"], p["x"], p["y"]) for p in layout["layers"] if p["kind"] == "caption"],
                         [("끝 자막", 72, 520, 1500)])
        self.assertEqual(len([p for p in preview_layout(value, FONT, .65)["layers"] if p["kind"] == "caption"]), 0)
        for time in [-1, value["duration_seconds"], math.inf, True]:
            with self.subTest(time=time), self.assertRaises(ValueError):
                preview_layout(value, FONT, time)


@unittest.skipUnless(HAS_RENDER, "FFmpeg, ffprobe, Pillow, and a TrueType font are required")
class DesignPreviewRenderTests(unittest.TestCase):
    def test_edited_source_time_logo_text_and_mp4_agree_and_failure_preserves_png(self):
        from PIL import Image, ImageFont
        with tempfile.TemporaryDirectory(prefix="shorts design ") as temporary:
            root = Path(temporary)
            source, logo, path, preview = root / "source.mp4", root / "logo.png", root / "plan.json", root / "preview.png"
            Image.new("RGB", (16, 16), (0, 255, 100)).save(logo)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-f", "lavfi", "-i", "color=red:s=320x240:r=30:d=1",
                "-f", "lavfi", "-i", "color=green:s=320x240:r=30:d=1",
                "-f", "lavfi", "-i", "color=blue:s=320x240:r=30:d=1",
                "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
            ], check=True, capture_output=True)
            value = {**make_plan(), "source": source.name, "logo": logo.name, "font": str(FONT),
                     "font_name": ImageFont.truetype(str(FONT)).getname()[0],
                     "design": {"title": {"y": 160, "size": 48, "line_gap": 60},
                                "caption": {"x": 400, "y": 1490, "punchline_size": 64},
                                "logo": {"x": 80, "y": 1690, "size": 100}}}
            save(path, value)
            info = render_preview(path, preview, .9)
            self.assertAlmostEqual(info["source_time"], 102.4)
            with Image.open(preview) as image:
                image = image.convert("RGB")
                self.assertEqual(image.size, (1080, 1920))
                self.assertGreater(image.getpixel((540, 900))[2], 240)
                self.assertGreater(image.getpixel((100, 1720))[1], 240)
                self.assertLess(max(image.getpixel((400, 1600))), 10)
                self.assertGreater(max(image.crop((200, 100, 800, 220)).getextrema()[0]), 200)
                self.assertLess(max(image.crop((200, 360, 800, 520)).getextrema()[0]), 10)
                self.assertGreater(max(image.crop((200, 1450, 650, 1530)).getextrema()[1]), 200)
            row = render_plan(path, root / "render")
            self.assertEqual(row["design"]["logo"], {"x": 80, "y": 1690, "size": 100})
            still = root / "rendered.png"
            subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.9", "-i", str(root / "render/design.mp4"),
                            "-frames:v", "1", "-update", "1", str(still)], check=True, capture_output=True)
            with Image.open(still) as image:
                self.assertGreater(image.getpixel((100, 1720))[1], 220)
                self.assertGreater(image.getpixel((540, 900))[2], 220)
                self.assertLess(max(image.crop((200, 360, 800, 520)).getextrema()[0]), 10)
                self.assertGreater(max(image.crop((200, 1450, 650, 1530)).getextrema()[1]), 200)
            previous = preview.read_bytes()
            original_run = subprocess.run

            def fail_ffmpeg(args, **kwargs):
                if args[0] == "ffmpeg":
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="preview failure")
                return original_run(args, **kwargs)

            with patch("chzzk_demo.render.subprocess.run", side_effect=fail_ffmpeg), self.assertRaisesRegex(RuntimeError, "이전 출력 보존"):
                render_preview(path, preview, .9)
            self.assertEqual(preview.read_bytes(), previous)
            self.assertFalse(any(p.name.startswith(".preview-") for p in root.iterdir()))
            with self.assertRaisesRegex(ValueError, "입력 파일과 같습니다"):
                render_preview(path, logo, .9)
            render_preview(path, preview, .2)
            with Image.open(preview) as image:
                self.assertGreater(image.getpixel((540, 900))[0], 240)


if __name__ == "__main__":
    unittest.main()
