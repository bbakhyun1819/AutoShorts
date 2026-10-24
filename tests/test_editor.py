import copy
import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from chzzk_demo.core import read, save
from chzzk_demo.editor import EditorLibrary, EditorSession, byte_range, make_server


FONT = next((Path(p) for p in ["C:/Windows/Fonts/malgunbd.ttf",
                              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"] if Path(p).is_file()), None)
HAS_FONT = bool(FONT and importlib.util.find_spec("PIL"))


class EditorCliTests(unittest.TestCase):
    def test_plan_and_library_are_required_alternatives(self):
        from chzzk_demo.cli import main

        for arguments in [[], ["--plan", "plan.json", "--library", "renders"]]:
            with self.subTest(arguments=arguments), \
                    patch("sys.argv", ["chzzk-demo", "editor", *arguments]), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)

    def test_library_and_single_plan_dispatch_without_opening_browser(self):
        from chzzk_demo.cli import main

        for mode, plan, library in [("--plan", "input.json", None), ("--library", None, "input.json")]:
            with self.subTest(mode=mode), \
                    patch("sys.argv", ["chzzk-demo", "editor", mode, "input.json", "--output", "edits",
                                       "--port", "8765", "--no-open"]), \
                    patch("chzzk_demo.editor.serve_editor") as serve:
                main()
                serve.assert_called_once_with(plan, "edits", library_path=library, port=8765, open_browser=False)


class ByteRangeTests(unittest.TestCase):
    def test_full_open_closed_suffix_and_clamped_ranges(self):
        for header, expected in [(None, (0, 9, False)), ("bytes=2-5", (2, 5, True)),
                                 ("bytes=8-", (8, 9, True)), ("bytes=-3", (7, 9, True)),
                                 ("bytes=-20", (0, 9, True)), ("bytes=7-200", (7, 9, True))]:
            with self.subTest(header=header):
                self.assertEqual(byte_range(header, 10), expected)
        self.assertEqual(byte_range(None, 0), (0, -1, False))

    def test_rejects_unsatisfiable_multiple_and_malformed_ranges(self):
        for header in ["bytes=10-", "bytes=8-3", "bytes=-0", "bytes=-", "bytes=0-1,5-6",
                       "items=0-1", "bytes=two-three", "bytes=+1-4", "bytes=1-2\n"]:
            with self.subTest(header=header), self.assertRaises(ValueError):
                byte_range(header, 10)
        with self.assertRaises(ValueError):
            byte_range("bytes=0-", 0)


@unittest.skipUnless(HAS_FONT, "Pillow and a TrueType font are needed for editor tests")
class EditorHttpTests(unittest.TestCase):
    """Exercise real loopback HTTP, with only FFprobe/encoding replaced by fixtures."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shorts editor ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(bytes(range(256)) * 4)
        self.logo = self.root / "logo.png"
        from PIL import Image, ImageFont
        Image.new("RGBA", (16, 16), (0, 255, 100, 255)).save(self.logo)
        self.font = self.root / "original.ttf"
        shutil.copyfile(FONT, self.font)
        self.plan_path = self.root / "original.json"
        self.original = dict(slug="test-short", source="source.mp4", logo="logo.png", font="original.ttf",
                             font_name=ImageFont.truetype(str(self.font), 32).getname()[0],
                             title_lines=["제목", "마지막 반응"], channel="채널", source_start=100,
                             keep=[[100, 101], [102, 103]],
                             captions=[dict(start=100, end=100.8, text="첫 대사"),
                                       dict(start=102, end=102.8, text="마지막 대사", style="Punchline")])
        save(self.plan_path, self.original)
        self.source_bytes = self.source.read_bytes()
        self.plan_bytes = self.plan_path.read_bytes()
        info = dict(format=dict(duration="3"), streams=[dict(codec_type="video", width=320, height=240,
                                                            duration="3"), dict(codec_type="audio")])
        self.probe = patch("chzzk_demo.render.probe", return_value=info)
        self.probe.start()
        self.addCleanup(self.probe.stop)
        self.font_discovery = patch.object(EditorSession, "_discover_fonts")
        self.font_discovery.start()
        self.addCleanup(self.font_discovery.stop)
        self.session = EditorSession(self.plan_path, self.root / "editor")
        self.server = make_server(self.session)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, *, data=None, headers=None, method=None, token=True):
        request_headers = {"X-Editor-Token": self.session.token} if token else {}
        request_headers.update(headers or {})
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode()
            request_headers["Content-Type"] = "application/json"
        request = Request(self.base + path, data=data, headers=request_headers, method=method)
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, dict(response.headers), response.read()

    def json_request(self, path, **kwargs):
        status, _, body = self.request(path, **kwargs)
        return status, json.loads(body)

    def catalog_fixture(self, *, incomplete=False):
        first, second = self.root / "first.mp4", self.root / "second.mp4"
        first.write_bytes(b"first rendered video")
        second.write_bytes(b"second rendered video")
        second_source = self.root / "second-source.mp4"
        second_source.write_bytes(b"second source video")
        second_plan = self.root / "second.json"
        save(second_plan, {**self.session.raw, "source": str(second_source),
                           "slug": "second", "title_lines": ["두 번째 후보"]})
        catalog = self.root / "library.json"
        rows = [dict(title="첫 번째", video=first.name, plan=self.plan_path.name),
                dict(title="두 번째", video=second.name, plan=second_plan.name)]
        if incomplete:
            playback = self.root / "playback-only.mp4"
            playback.write_bytes(b"playback only")
            bad_assets = self.root / "bad-assets.mp4"
            bad_assets.write_bytes(b"missing source")
            bad_plan = self.root / "bad-assets.json"
            save(bad_plan, {**self.session.raw, "source": "missing-source.mp4"})
            rows += [dict(title="재생 전용", video=playback.name, plan="missing-plan.json"),
                     dict(title="원본 없음", video=bad_assets.name, plan=bad_plan.name),
                     dict(title="영상 없음", video="missing.mp4", plan=self.plan_path.name)]
        save(catalog, dict(schema_version=1, items=rows))
        return catalog

    def use_library(self, library):
        self.stop_server()
        self.session = library  # request() authenticates with the catalog's token.
        self.server = make_server(library, workspace=self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def test_state_defaults_assets_and_token_host_origin_boundaries(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, state = self.json_request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["duration"], 2)
        self.assertEqual(state["source_bounds"], {"start": 100, "end": 103})
        self.assertEqual(state["original_keep"], self.original["keep"])
        self.assertEqual(state["layout"]["panel"], [0, 555, 1080, 810])
        self.assertEqual(state["normalized"]["design"]["title"]["size"], 76)
        self.assertEqual(state["normalized"]["design"]["caption"]["y"], 1435)
        self.assertTrue(all("path" not in font for font in state["fonts"]))
        for options in [dict(token=False), dict(headers={"X-Editor-Token": "wrong"}),
                        dict(headers={"Host": "attacker.example"}),
                        dict(headers={"Origin": "https://attacker.example"})]:
            with self.subTest(options=options):
                self.assertEqual(self.request("/api/state", **options)[0], 403)
                self.assertEqual(self.request("/media/source", **options)[0], 403)
                self.assertEqual(self.request("/api/save", data={}, **options)[0], 403)
        self.assertEqual(self.request("/api/state", headers={"Origin": self.base})[0], 200)
        self.assertEqual(self.request("/media/source?token=" + self.session.token, token=False)[0], 200)

    def test_media_bytes_range_headers_and_head(self):
        size = len(self.source_bytes)
        status, headers, body = self.request("/media/source", headers={"Range": "bytes=17-32"})
        self.assertEqual((status, body), (206, self.source_bytes[17:33]))
        self.assertEqual(headers["Content-Range"], f"bytes 17-32/{size}")
        self.assertEqual(headers["Content-Length"], "16")
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        status, _, body = self.request("/media/source", headers={"Range": "bytes=-7"})
        self.assertEqual((status, body), (206, self.source_bytes[-7:]))
        status, headers, body = self.request("/media/source", headers={"Range": "bytes=2000-"})
        self.assertEqual((status, body), (416, b""))
        self.assertEqual(headers["Content-Range"], f"bytes */{size}")
        status, headers, body = self.request("/media/source", method="HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(int(headers["Content-Length"]), size)

    def test_output_whitelist_rejects_path_traversal(self):
        renders = self.session.output / "renders"
        renders.mkdir()
        (renders / "test-short.mp4").write_bytes(b"rendered")
        self.assertEqual(self.request("/output/test-short.mp4")[2], b"rendered")
        for path in ["/output/../edit-plan.json", "/output/%2e%2e/original.json",
                     "/output/test-short.json", "/media/../source.mp4", "/media/font?id=unknown"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_font_upload_save_preserves_inputs_and_restart_restores_design(self):
        status, uploaded = self.json_request("/api/font?name=selected.ttf", data=self.font.read_bytes())
        self.assertEqual(status, 200)
        selected = uploaded["active_font"]
        self.assertNotEqual(selected, self.session.font_id)
        design = copy.deepcopy(uploaded["default_design"])
        design["title"].update(x=475, y=360, size=64)
        design["caption"].update(y=1460, size=52)
        design["logo"].update(x=250, y=1600)
        status, saved = self.json_request("/api/save", data=dict(font_id=selected, design=design))
        self.assertEqual(status, 200)
        draft = read(self.session.draft_path)
        self.assertEqual(draft["design"], design)
        self.assertEqual(draft["font"], str(self.session.fonts[selected]["path"]))
        self.assertEqual(draft["font_name"], self.session.fonts[selected]["family"])
        for key in ("keep", "captions", "source_start", "title_lines"):
            self.assertEqual(draft[key], self.original[key])
        self.assertEqual(self.plan_path.read_bytes(), self.plan_bytes)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        restarted = EditorSession(self.plan_path, self.session.output)
        self.assertEqual(restarted.state()["plan"], draft)
        self.assertEqual(restarted.state()["active_font"], selected)
        self.assertEqual(saved["saved_path"], str(self.session.draft_path))

    def test_rejects_unknown_font_injected_paths_invalid_design_and_bad_font(self):
        for payload in [dict(font_id="unknown"), dict(source="C:/private.mp4"), dict(font="C:/private.ttf"),
                        dict(keep=[[100, 104]]), dict(source_start=99), dict(logo="private.png"),
                        dict(crop={"x": 0}), dict(slug="changed"), dict(font_name="other"),
                        dict(design={"title": {"size": -1}}), []]:
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/api/save", data=payload)[0], 400)
        self.assertFalse(self.session.draft_path.exists())
        self.assertEqual(self.request("/api/font?name=bad.ttf", data=b"not a font")[0], 400)
        self.assertEqual(self.request("/api/font?name=bad.exe", data=self.font.read_bytes())[0], 400)
        self.assertFalse(any((self.session.output / "fonts").iterdir()))
        self.assertEqual(self.plan_path.read_bytes(), self.plan_bytes)

    def test_save_never_overwrites_input_plan_with_matching_draft_name(self):
        path = self.root / "edit-plan.json"
        save(path, self.original)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            session = EditorSession(path, self.root)
            session.save_edit({})
        self.assertEqual(path.read_bytes(), before)

    def test_save_rejects_temporary_file_collision_with_source_or_input_plan(self):
        collision = self.session.output / "edit-plan.json.tmp"
        for asset in ("source", "plan"):
            with self.subTest(asset=asset):
                if asset == "source":
                    collision.write_bytes(self.source_bytes)
                    plan_path = self.root / "temporary-source-plan.json"
                    save(plan_path, {**self.session.raw, "source": str(collision)})
                else:
                    plan_path = collision
                    save(plan_path, self.session.raw)
                session = EditorSession(plan_path, self.session.output)
                before = collision.read_bytes()
                with self.assertRaisesRegex(ValueError, "입력 파일과 겹칩니다"):
                    session.save_edit({"design": {"title": {"x": 450}}})
                self.assertEqual(collision.read_bytes(), before)
                self.assertFalse(session.draft_path.exists())
                collision.unlink()
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertEqual(self.plan_path.read_bytes(), self.plan_bytes)

    def test_render_rejects_original_plan_at_destination_before_saving_or_encoding(self):
        plan_path = self.session.output / "renders" / "test-short.json"
        save(plan_path, self.session.raw)
        session = EditorSession(plan_path, self.session.output)
        before = plan_path.read_bytes()
        with patch("chzzk_demo.editor.render_plan") as encode, \
                self.assertRaisesRegex(ValueError, "원본 편집안"):
            session.start_render({"design": {"title": {"x": 450}}})
        encode.assert_not_called()
        self.assertEqual(plan_path.read_bytes(), before)
        self.assertEqual(session.job, {"state": "idle"})
        self.assertFalse(session.draft_path.exists())
        self.assertFalse((session.output / "requests").exists())

    def test_restart_rejects_draft_from_different_source_identity_or_offset(self):
        for changed in [dict(source=str(self.root / "other.mp4")), dict(source_start=99), dict(slug="other"),
                        dict(logo=str(self.root / "other.png")), dict(crop={"x": 0, "y": 0, "width": 320, "height": 240})]:
            with self.subTest(changed=changed):
                save(self.session.draft_path, {**self.session.raw, **changed})
                with self.assertRaisesRegex(ValueError, "다른 영상"):
                    EditorSession(self.plan_path, self.session.output)

    def test_content_and_cuts_save_in_source_time_and_restore_without_changing_inputs(self):
        payload = dict(title_lines=["바꾼 제목"], channel="새 채널", keep=[[100.1, 101.5], [102.1, 102.9]],
                       captions=[dict(start=100.2, end=101.2, text="수정한 대사", style="Emphasis"),
                                 dict(start=102.2, end=102.8, text="끝 대사", style="Punchline")],
                       context=[dict(start=.1, end=.8, text="맥락 설명")])
        status, state = self.json_request("/api/save", data=payload)
        self.assertEqual(status, 200)
        self.assertAlmostEqual(state["duration"], 2.2)
        for key, value in payload.items():
            self.assertEqual(state["plan"][key], value)
        self.assertEqual(state["original_keep"], self.original["keep"])
        self.assertEqual(state["source_bounds"], {"start": 100, "end": 103})
        self.assertEqual(state["plan"]["_editor_origin"], {"plan_path": str(self.plan_path)})
        restarted = EditorSession(self.plan_path, self.session.output)
        self.assertEqual(restarted.state()["plan"], state["plan"])
        self.assertEqual(self.plan_path.read_bytes(), self.plan_bytes)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def test_draft_origin_rejects_another_plan_with_the_same_source_slug_and_offsets(self):
        self.session.save_edit(dict(title_lines=["첫 번째 편집안"], keep=[[100, 103]]))
        different_plan = self.root / "other-original.json"
        save(different_plan, {**self.original, "title_lines": ["다른 후보"], "keep": [[100, 103]]})
        with self.assertRaisesRegex(ValueError, "다른 원본 편집안"):
            EditorSession(different_plan, self.session.output)
        self.assertEqual(read(self.session.draft_path)["title_lines"], ["첫 번째 편집안"])
        for origin in (None, {}, {"plan_path": "original.json"}, {"plan_path": str(different_plan)}):
            with self.subTest(origin=origin):
                save(self.session.draft_path, {**self.session.raw, "_editor_origin": origin})
                with self.assertRaisesRegex(ValueError, "다른 원본 편집안"):
                    EditorSession(self.plan_path, self.session.output)

    def test_legacy_draft_restores_matching_original_cuts_and_rejects_ambiguous_changed_cuts(self):
        legacy = {**self.session.raw, "design": {"title": {"x": 450}}}
        save(self.session.draft_path, legacy)
        restored = EditorSession(self.plan_path, self.session.output)
        self.assertEqual(restored.state()["normalized"]["design"]["title"]["x"], 450)
        restored.save_edit(dict(keep=[[100, 103]]))
        self.assertEqual(EditorSession(self.plan_path, self.session.output).state()["plan"]["keep"], [[100, 103]])
        save(self.session.draft_path, {**legacy, "keep": [[100, 103]]})
        with self.assertRaisesRegex(ValueError, "이전 형식"):
            EditorSession(self.plan_path, self.session.output)

    def test_content_validation_rejects_invalid_cues_and_never_silently_clips(self):
        invalid = [dict(keep=[[100.2, 100.6]]),  # Existing cues no longer fit.
                   dict(keep=[[100, 102], [101.5, 103]]),
                   dict(keep=[[0, 1]], captions=[]), dict(keep=[[100, 103.1]]),
                   dict(captions=[dict(start=100, end=102.5, text="컷 경계")]),
                   dict(captions=[dict(start=100, end=100.5, text="첫 줄"),
                                  dict(start=100.4, end=100.8, text="겹친 줄")]),
                   dict(captions=[dict(start=100, end=100.5, text="", style="Caption")]),
                   dict(context=[dict(start=100, end=101, text="원본 시각은 사용할 수 없음")]),
                   dict(title_lines=[]), dict(channel="")]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/api/layout", data=payload)[0], 400)
                self.assertEqual(self.request("/api/save", data=payload)[0], 400)
        self.assertFalse(self.session.draft_path.exists())
        self.assertEqual(self.session.raw["captions"], self.original["captions"])
        status, state = self.json_request("/api/save", data=dict(keep=[[100.2, 100.6]], captions=[]))
        self.assertEqual(status, 200)
        self.assertEqual(state["plan"]["captions"], [])

    def test_layout_returns_unsaved_timeline_geometry_and_accepts_larger_content_payload(self):
        payload = dict(title_lines=["저장하지 않은 제목"], keep=[[100, 103]],
                       captions=[dict(start=101, end=101.8, text="중간 대사")], time=1.2)
        status, result = self.json_request("/api/layout", data=json.dumps(payload).encode() + b" " * 70000)
        self.assertEqual(status, 200)
        self.assertEqual(result["duration"], 3)
        self.assertEqual(result["normalized"]["title_lines"], payload["title_lines"])
        self.assertTrue(any(layer["text"] == "중간 대사" for layer in result["layout"]["layers"]))
        self.assertEqual(self.session.raw["keep"], self.original["keep"])
        self.assertFalse(self.session.draft_path.exists())
        self.assertEqual(self.request("/api/layout", data={**payload, "time": 3})[0], 400)
        self.assertEqual(self.request("/api/layout", data=b"{}",
                                      headers={"Content-Length": str(2 * 1024 * 1024 + 1)})[0], 400)
        self.assertEqual(self.request("/api/layout", data=payload, token=False)[0], 403)

    def test_preview_passes_current_design_time_and_preserves_unsaved_plan(self):
        def fake_preview(plan, output, moment):
            value = read(plan)
            self.assertEqual(value["design"]["title"]["x"], 450)
            self.assertEqual(moment, 1.5)
            Path(output).write_bytes(b"fixture png")

        with patch("chzzk_demo.editor.render_preview", side_effect=fake_preview):
            status, headers, data = self.request("/api/preview", data=dict(design={"title": {"x": 450}}, time=1.5))
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(data, b"fixture png")
        self.assertFalse(self.session.draft_path.exists())
        self.assertFalse(any(self.session.output.glob("preview-*")))

    def test_render_is_async_uses_snapshot_and_reports_success_or_failure(self):
        entered, release = threading.Event(), threading.Event()
        captured = []

        def fake_render(plan, output, *, gallery):
            captured.append(read(plan))
            self.assertTrue(gallery)
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test release timeout")
            output.mkdir(exist_ok=True)
            (output / "test-short.mp4").write_bytes(b"generated video")
            return {"video": "test-short.mp4"}

        with patch("chzzk_demo.editor.render_plan", side_effect=fake_render):
            try:
                status, started = self.json_request("/api/render", data=dict(design={"title": {"x": 450}}))
                self.assertEqual(status, 202)
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.json_request("/api/render-status")[1]["state"], "running")
                self.assertEqual(self.request("/api/render", data={})[0], 400)
                self.assertEqual(self.request("/api/save", data=dict(design={"title": {"x": 500}}))[0], 200)
                self.assertEqual(captured[0]["design"]["title"]["x"], 450)
            finally:
                release.set()
            state = self.wait_for_render()
        self.assertEqual(state["state"], "done")
        self.assertEqual(state["job_id"], started["job_id"])
        self.assertEqual(self.request(state["url"], token=False)[2], b"generated video")
        with patch("chzzk_demo.editor.render_plan", side_effect=RuntimeError("encoder failure")):
            self.assertEqual(self.request("/api/render", data={})[0], 202)
            state = self.wait_for_render()
        self.assertEqual(state["state"], "error")
        self.assertIn("encoder failure", state["error"])

    def wait_for_render(self, item=None):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = self.json_request("/api/render-status" + ("?item=" + item if item else ""))[1]
            if state["state"] != "running":
                return state
            time.sleep(.01)
        self.fail("render job did not finish")

    def test_library_lists_playback_only_entries_without_selecting_a_video(self):
        library = EditorLibrary(self.catalog_fixture(incomplete=True), self.root / "library-edits")
        self.use_library(library)
        status, result = self.json_request("/api/library")
        self.assertEqual(status, 200)
        self.assertEqual(len(result["items"]), 4)
        items = {item["title"]: item for item in result["items"]}
        self.assertTrue(items["첫 번째"]["editable"])
        self.assertTrue(items["두 번째"]["editable"])
        self.assertFalse(items["재생 전용"]["editable"])
        self.assertFalse(items["원본 없음"]["editable"])
        self.assertTrue(items["재생 전용"]["reason"])
        self.assertEqual(library.sessions, {})
        self.assertEqual(self.request("/api/state")[0], 400)
        self.assertEqual(self.request("/api/library", token=False)[0], 403)
        readonly = items["재생 전용"]
        self.assertEqual(self.request(readonly["video_url"], token=False)[2], b"playback only")
        self.assertEqual(self.request("/api/state?item=" + readonly["id"])[0], 400)
        self.assertEqual(self.request("/api/save?item=" + readonly["id"], data={})[0], 400)

    def test_library_item_media_and_saved_designs_remain_separate_after_restart(self):
        catalog = self.catalog_fixture()
        library = EditorLibrary(catalog, self.root / "library-edits")
        self.use_library(library)
        items = {item["title"]: item for item in self.json_request("/api/library")[1]["items"]}
        first, second = items["첫 번째"]["id"], items["두 번째"]["id"]
        self.assertNotEqual(first, second)
        status, state = self.json_request("/api/state?item=" + first)
        self.assertEqual((status, state["item_id"]), (200, first))
        self.assertEqual(self.request("/media/source?item=" + first)[2], self.source_bytes)
        self.assertEqual(self.request("/media/source?item=" + second)[2], b"second source video")
        self.assertEqual(self.request("/media/rendered?item=" + first, headers={"Range": "bytes=0-4"})[2], b"first")
        for item, x in [(first, 450), (second, 600)]:
            self.assertEqual(self.request("/api/save?item=" + item,
                                          data=dict(design={"title": {"x": x}}))[0], 200)
        first_session, second_session = library.get_session(first), library.get_session(second)
        self.assertEqual(first_session.draft_path, library.output / "items" / first / "edit-plan.json")
        self.assertEqual(second_session.draft_path, library.output / "items" / second / "edit-plan.json")
        self.assertEqual(read(first_session.draft_path)["design"]["title"]["x"], 450)
        self.assertEqual(read(second_session.draft_path)["design"]["title"]["x"], 600)
        restarted = EditorLibrary(catalog, library.output)
        self.assertEqual(set(restarted.entries), {first, second})
        self.assertEqual(restarted.get_session(first).state()["normalized"]["design"]["title"]["x"], 450)
        self.assertEqual(restarted.get_session(second).state()["normalized"]["design"]["title"]["x"], 600)
        self.assertEqual(self.plan_path.read_bytes(), self.plan_bytes)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def test_library_unknown_or_missing_item_never_defaults_to_first_video(self):
        library = EditorLibrary(self.catalog_fixture(), self.root / "library-edits")
        self.use_library(library)
        for query in ["", "?item=unknown", "?item=..%2Fsource.mp4", "?item=C%3A%2Fprivate.json"]:
            with self.subTest(query=query):
                for endpoint in ("/api/state", "/api/render-status", "/media/source", "/media/rendered"):
                    self.assertEqual(self.request(endpoint + query)[0], 400)
                self.assertEqual(self.request("/api/save" + query, data={})[0], 400)
                self.assertEqual(self.request("/api/render" + query, data={})[0], 400)
        self.assertEqual(library.sessions, {})
        self.assertFalse((library.output / "items").exists())

    def test_library_directory_manifests_skip_invalid_and_own_edited_duplicates(self):
        library_root = self.root / "collection"
        nested = library_root / "first-batch"
        nested.mkdir(parents=True)
        (nested / "test-short.mp4").write_bytes(b"rendered")
        row = dict(video="test-short.mp4", plan_file=str(self.plan_path), slug="test-short",
                   title_lines=["렌더된 후보"], duration_seconds=2)
        save(nested / "manifest.json", dict(schema_version=1, renders=[row]))
        output = library_root / "edits"
        duplicate = output / "items" / "existing" / "renders"
        duplicate.mkdir(parents=True)
        (duplicate / "test-short.mp4").write_bytes(b"edited duplicate")
        save(duplicate / "manifest.json", dict(schema_version=1, renders=[row]))
        malformed = library_root / "unsupported"
        malformed.mkdir()
        save(malformed / "manifest.json", ["not a render manifest"])
        library = EditorLibrary(library_root, output)
        result = library.list_items()
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(result["items"][0]["editable"])
        self.assertEqual(len(result["warnings"]), 1)
        with self.assertRaisesRegex(ValueError, "목록"):
            EditorLibrary(malformed / "manifest.json", self.root / "bad-library")

    def test_library_preview_only_uses_matching_safe_completed_render(self):
        library = EditorLibrary(self.catalog_fixture(), self.root / "library-edits")
        self.use_library(library)
        item = self.json_request("/api/library")[1]["items"][0]
        item_id = item["id"]
        renders = library.output / "items" / item_id / "renders"
        renders.mkdir(parents=True)
        (renders / "test-short.mp4").write_bytes(b"new completed render")
        for row in [dict(slug="different", video="test-short.mp4"),
                    dict(slug="test-short", video="../private.mp4")]:
            with self.subTest(row=row):
                save(renders / "manifest.json", dict(renders=[row]))
                self.assertEqual(self.request(item["video_url"], token=False)[2], b"first rendered video")
        save(renders / "manifest.json", dict(renders=[dict(slug="test-short", video="test-short.mp4")]))
        self.assertEqual(self.request(item["video_url"], token=False)[2], b"new completed render")

    def test_library_render_status_and_output_link_belong_to_selected_item(self):
        library = EditorLibrary(self.catalog_fixture(), self.root / "library-edits")
        self.use_library(library)
        items = self.json_request("/api/library")[1]["items"]
        first, second = items[0]["id"], items[1]["id"]

        def fake_render(plan, output, *, gallery):
            self.assertEqual(read(plan)["slug"], "test-short")
            self.assertEqual(output, library.output / "items" / first / "renders")
            output.mkdir(parents=True, exist_ok=True)
            row = dict(slug="test-short", video="test-short.mp4")
            (output / row["video"]).write_bytes(b"first item result")
            save(output / "manifest.json", dict(renders=[row]))
            return row

        with patch("chzzk_demo.editor.render_plan", side_effect=fake_render):
            self.assertEqual(self.request("/api/render?item=" + first, data={})[0], 202)
            result = self.wait_for_render(first)
        self.assertEqual(result["state"], "done")
        self.assertIn("&item=" + first, result["url"])
        self.assertEqual(self.request(result["url"], token=False)[2], b"first item result")
        self.assertEqual(self.json_request("/api/render-status?item=" + second)[1]["state"], "idle")
        self.assertEqual(self.request(result["url"].replace("&item=" + first, "&item=" + second), token=False)[0], 404)
        self.assertEqual(self.request(items[0]["video_url"], token=False)[2], b"first item result")
        self.assertEqual(self.request(items[1]["video_url"], token=False)[2], b"second rendered video")

    def test_single_plan_library_preserves_existing_draft_location(self):
        self.session.save_edit(dict(design={"title": {"x": 475}}))
        output = self.session.output
        library = EditorLibrary.from_plan(self.plan_path, output)
        self.use_library(library)
        result = self.json_request("/api/library")[1]
        self.assertEqual(len(result["items"]), 1)
        self.assertIsNone(result["items"][0]["video_url"])
        self.assertEqual(library.sessions, {})
        item_id = result["items"][0]["id"]
        status, state = self.json_request("/api/state?item=" + item_id)
        self.assertEqual(status, 200)
        self.assertEqual(state["normalized"]["design"]["title"]["x"], 475)
        self.assertEqual(library.get_session(item_id).draft_path, output / "edit-plan.json")

    def test_library_add_plan_keeps_catalog_immutable_and_restores_plan_without_mp4(self):
        catalog = self.catalog_fixture()
        before = catalog.read_bytes()
        output = self.root / "library-edits"
        library = EditorLibrary(catalog, output)
        imported = self.root / "new-plan.json"
        save(imported, {**self.session.raw, "slug": "new-short", "title_lines": ["아직 렌더하지 않은 후보"]})
        added = library.add_plan(imported)
        self.assertEqual(len(library.entries), 3)
        self.assertEqual(read(output / "imports" / "catalog.json")["items"], [{"plan": str(imported)}])
        self.assertEqual(library.add_plan(imported)["id"], added["id"])
        self.assertEqual(len(read(output / "imports" / "catalog.json")["items"]), 1)
        self.assertEqual(catalog.read_bytes(), before)
        restarted = EditorLibrary(catalog, output)
        item = next(item for item in restarted.list_items()["items"] if item["id"] == added["id"])
        self.assertTrue(item["editable"])
        self.assertIsNone(item["video_url"])
        session = restarted.get_session(added["id"])
        self.assertEqual(session.draft_path, output / "items" / added["id"] / "edit-plan.json")
        self.assertEqual(session.plan_path, imported)

    def test_library_add_plan_rejects_invalid_plan_and_import_output_input_collision(self):
        library = EditorLibrary(self.catalog_fixture(), self.root / "library-edits")
        invalid = self.root / "invalid-plan.json"
        save(invalid, {**self.session.raw, "keep": [[100, 104]]})
        with self.assertRaises(ValueError):
            library.add_plan(invalid)
        destination = library.output / "imports" / "catalog.json"
        save(destination, self.session.raw)
        before = destination.read_bytes()
        with self.assertRaisesRegex(ValueError, "겹칩니다"):
            library.add_plan(destination)
        self.assertEqual(destination.read_bytes(), before)
        valid = self.root / "valid-new-plan.json"
        save(valid, self.session.raw)
        save(destination, {"items": [42]})
        malformed = destination.read_bytes()
        with self.assertRaisesRegex(ValueError, "잘못된 항목"):
            library.add_plan(valid)
        self.assertEqual(destination.read_bytes(), malformed)

    def test_library_render_protects_original_catalog(self):
        output = self.root / "protected-editor"
        catalog = output / "items" / "placeholder" / "renders" / "test-short.json"
        save(catalog, dict(items=[dict(plan=str(self.plan_path))]))
        library = EditorLibrary(catalog, output)
        item_id = next(iter(library.entries))
        target_output = catalog.parent.parent
        session = library.get_session(item_id)
        session.output = target_output
        before = catalog.read_bytes()
        with patch("chzzk_demo.editor.render_plan") as encode, self.assertRaisesRegex(ValueError, "원본 편집안"):
            session.start_render({})
        encode.assert_not_called()
        self.assertEqual(catalog.read_bytes(), before)

    def test_single_plan_library_imports_use_separate_output_and_keep_original_session_location(self):
        library = EditorLibrary.from_plan(self.plan_path, self.session.output)
        original_id = next(iter(library.entries))
        added_path = self.root / "added.json"
        save(added_path, {**self.session.raw, "slug": "added", "title_lines": ["추가 후보"]})
        added = library.add_plan(added_path)
        self.assertNotEqual(added["id"], original_id)
        self.assertEqual(library.get_session(original_id).output, library.output)
        self.assertEqual(library.get_session(added["id"]).output, library.output / "items" / added["id"])
        restarted = EditorLibrary.from_plan(self.plan_path, library.output)
        self.assertEqual(set(restarted.entries), {original_id, added["id"]})

    def test_library_title_and_duration_follow_saved_draft_even_before_open_after_restart(self):
        catalog = self.catalog_fixture()
        library = EditorLibrary(catalog, self.root / "library-edits")
        item_id = next(iter(library.entries))
        library.get_session(item_id).save_edit(dict(title_lines=["새 제목", "새 결말"], keep=[[100, 103]]))
        for current in (library, EditorLibrary(catalog, library.output)):
            item = next(item for item in current.list_items()["items"] if item["id"] == item_id)
            self.assertEqual(item["title"], "새 제목 새 결말")
            self.assertEqual(item["title_lines"], ["새 제목", "새 결말"])
            self.assertEqual(item["duration"], 3)

    def test_project_routes_are_authenticated_and_dispatch_without_selecting_an_editor_item(self):
        library = EditorLibrary(self.catalog_fixture(), self.root / "library-edits")
        with patch("chzzk_demo.pipeline.PipelineManager") as factory:
            manager = factory.return_value
            manager.list_projects.return_value = {"projects": [{"id": "job-a"}]}
            manager.detail.return_value = {"id": "job-a", "stage": "prepared"}
            manager.create.return_value = {"id": "created"}
            manager.action.return_value = {"saved": True}
            self.use_library(library)
            factory.assert_called_once_with(library, workspace=self.root)
        for endpoint in ("/api/projects", "/api/project?id=job-a", "/api/project/file?id=job-a&name=report"):
            self.assertEqual(self.request(endpoint, token=False)[0], 403)
        self.assertEqual(self.request("/api/projects", data={}, token=False)[0], 403)
        manager.list_projects.assert_not_called()
        manager.create.assert_not_called()
        self.assertEqual(self.json_request("/api/projects")[1], {"projects": [{"id": "job-a"}]})
        self.assertEqual(self.json_request("/api/project?id=job-a")[1]["stage"], "prepared")
        manager.detail.assert_called_once_with("job-a")
        self.assertEqual(self.json_request("/api/projects", data=dict(title="새 작업"))[1], {"id": "created"})
        manager.create.assert_called_once_with(dict(title="새 작업"))
        self.assertEqual(self.json_request("/api/project/action?id=job-a", data=dict(action="prepare"))[1], {"saved": True})
        manager.action.assert_called_with("job-a", dict(action="prepare"))
        self.assertEqual(self.request("/api/project/action", data=dict(action="save-defaults"))[0], 200)
        manager.action.assert_called_with("", dict(action="save-defaults"))
        self.assertEqual(self.request("/api/projects", data=b"{}" + b" " * (2 * 1024 * 1024 + 1))[0], 200)
        self.assertEqual(self.request("/api/projects", data=b"{}",
                                      headers={"Content-Length": str(10 * 1024 * 1024 + 1)})[0], 400)
        self.assertEqual(library.sessions, {})

    def test_project_file_ranges_and_asset_streams_delegate_to_manager_before_session_lookup(self):
        library = EditorLibrary(self.catalog_fixture(), self.root / "library-edits")
        with patch("chzzk_demo.pipeline.PipelineManager") as factory:
            manager = factory.return_value
            manager.file_path.return_value = self.source

            def upload(stream, length, kind, name):
                self.assertEqual((length, kind, name), (9, "logo", "logo.png"))
                self.assertEqual(stream.read(length), b"test-logo")
                return {"asset_id": "fixture-logo"}

            manager.upload.side_effect = upload
            self.use_library(library)
        status, headers, content = self.request("/api/project/file?id=job-a&name=clip", headers={"Range": "bytes=2-6"})
        self.assertEqual((status, content), (206, self.source_bytes[2:7]))
        manager.file_path.assert_called_once_with("job-a", "clip")
        self.assertEqual(headers["Content-Range"], f"bytes 2-6/{len(self.source_bytes)}")
        self.assertEqual(self.json_request("/api/assets?kind=logo&name=logo.png", data=b"test-logo")[1],
                         {"asset_id": "fixture-logo"})
        manager.upload.assert_called_once()
        manager.file_path.side_effect = ValueError("지원하지 않는 프로젝트 파일입니다.")
        self.assertEqual(self.request("/api/project/file?id=job-a&name=..%2Fsecret")[0], 400)
        manager.upload.side_effect = ValueError("지원하지 않는 업로드입니다.")
        self.assertEqual(self.request("/api/assets?kind=unknown&name=file", data=b"bad")[0], 400)
        self.assertEqual(library.sessions, {})


if __name__ == "__main__":
    unittest.main()
