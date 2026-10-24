"""Loopback-only visual editor for a local Shorts edit plan."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import mimetypes
import os
import re
import secrets
import threading
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from .core import read, save
from .render import load_render_plan, preview_layout, render_plan, render_preview, validate_plan

MAX_FONT_BYTES = 20 * 1024 * 1024
MAX_EDIT_BYTES = 2 * 1024 * 1024
MAX_PROJECT_BYTES = 10 * 1024 * 1024
DRAFT_ORIGIN_KEY = "_editor_origin"


def _check_draft_origin(draft, original, plan_path):
    """Edited cuts may differ only when the draft identifies its original plan."""
    if DRAFT_ORIGIN_KEY in draft:
        origin = draft[DRAFT_ORIGIN_KEY]
        path = origin.get("plan_path") if isinstance(origin, dict) else None
        if not isinstance(path, str) or not Path(path).is_absolute() or Path(path) != plan_path:
            raise ValueError("저장된 편집안은 다른 원본 편집안에서 작성되었습니다. 해당 --plan 또는 다른 --output을 사용하세요.")
    elif draft.get("keep") != original.get("keep"):
        raise ValueError("이전 형식의 편집안과 원본 구간이 달라 출처를 확인할 수 없습니다. 원래 --plan 또는 다른 --output을 사용하세요.")


def byte_range(value, size):
    """Return one inclusive byte range, or raise for unsupported/unsatisfiable ranges."""
    if not value:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()) or size == 0:
        raise ValueError("Invalid byte range")
    first, last = match.groups()
    if not first:
        count = int(last)
        if count <= 0:
            raise ValueError("Invalid byte range")
        return max(0, size - count), size - 1, True
    start, end = int(first), min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ValueError("Invalid byte range")
    return start, end, True


class EditorSession:
    def __init__(self, plan_path, output_dir):
        self.plan_path = Path(plan_path).resolve()
        self.output = Path(output_dir).resolve()
        normalized, files, info = load_render_plan(self.plan_path)
        self.files, self.source_info = files, info
        self.original = read(self.plan_path)
        self.original.update({key: str(path) for key, path in files.items()})
        self.raw = copy.deepcopy(self.original)
        self.output.mkdir(parents=True, exist_ok=True)
        self.draft_path = self.output / "edit-plan.json"
        if self.draft_path.resolve() == self.plan_path:
            raise ValueError("편집안 저장 위치가 원본 편집안과 같습니다. 다른 --output을 지정하세요.")
        self.lock = threading.RLock()
        self.preview_lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.fonts = {}
        self.job = {"state": "idle"}
        self.protected_inputs = []
        self.font_id = self._add_font(files["font"])
        self.default_font = self.font_id
        self._discover_fonts()
        if self.draft_path.exists() and self.draft_path != self.plan_path:
            prior = read(self.draft_path)
            if (not isinstance(prior, dict) or prior.get("source") != self.original["source"] or
                    prior.get("source_start", 0) != self.original.get("source_start", 0) or
                    prior.get("slug", "short") != self.original.get("slug", "short") or
                    prior.get("logo") != self.original["logo"] or prior.get("crop") != self.original.get("crop")):
                raise ValueError("이 편집 폴더에는 다른 영상의 편집안이 있습니다. 다른 --output을 지정하세요.")
            _check_draft_origin(prior, self.original, self.plan_path)
            self.raw = prior
            self.font_id = self._add_font(Path(prior["font"]))
            self._normalize(self.raw)
        defaults = self._normalize({**self.original, "design": {}})
        self.default_design = self._resolved_design(defaults, files["font"])

    def _normalize(self, raw):
        video = next(s for s in self.source_info["streams"] if s.get("codec_type") == "video")
        return validate_plan(raw, source_duration=float(video.get("duration") or self.source_info["format"]["duration"]),
                             source_width=video["width"], source_height=video["height"])

    def _resolved_design(self, normalized, font):
        design = copy.deepcopy(normalized["design"])
        if design["channel"]["x"] is None:
            layers = preview_layout(normalized, font, 0)["layers"]
            design["channel"]["x"] = next(x["x"] for x in layers if x["id"] == "channel")
        return design

    def _add_font(self, path):
        from PIL import ImageFont
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
            raise ValueError("사용할 수 있는 TTF/OTF 글꼴 파일이 필요합니다.")
        family, style = ImageFont.truetype(str(path), 32).getname()
        fid = hashlib.sha256(str(path).encode()).hexdigest()[:20]
        label = style.lower()
        weight = 700 if any(word in label for word in ("bold", "black", "heavy", "demi")) else 400
        italic = "italic" if "italic" in label or "oblique" in label else "normal"
        self.fonts[fid] = {"id": fid, "name": f"{family} · {style}", "family": family,
                           "weight": weight, "style": italic, "path": path}
        return fid

    def _discover_fonts(self):
        roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                 Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Microsoft/Windows/Fonts",
                 Path("/usr/share/fonts/truetype"), Path.home() / ".local/share/fonts",
                 Path("/Library/Fonts"), self.output / "fonts"]
        for root in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.suffix.lower() not in {".ttf", ".otf"} or not path.is_file():
                    continue
                try:
                    self._add_font(path)
                except (OSError, ValueError):
                    continue

    def state(self):
        with self.lock:
            normalized = self._normalize(self.raw)
            font = self.fonts[self.font_id]["path"]
            normalized["design"] = self._resolved_design(normalized, font)
            video = next(s for s in self.source_info["streams"] if s.get("codec_type") == "video")
            duration = float(video.get("duration") or self.source_info["format"]["duration"])
            return {"plan": copy.deepcopy(self.raw), "normalized": normalized, "duration": normalized["duration_seconds"],
                    "source_bounds": {"start": normalized["source_start"], "end": normalized["source_start"] + duration},
                    "original_keep": copy.deepcopy(self.original["keep"]),
                    "default_design": self.default_design, "layout": preview_layout(normalized, font, 0),
                    "fonts": sorted(({k: v for k, v in f.items() if k != "path"} for f in self.fonts.values()),
                                    key=lambda x: x["name"].lower()),
                    "active_font": self.font_id, "filename": self.plan_path.name, "plan_path": str(self.plan_path),
                    "default_font": self.default_font,
                    "saved_path": str(self.draft_path) if self.draft_path.exists() else None}

    def edited(self, payload):
        content_keys = {"title_lines", "channel", "captions", "keep", "context"}
        if not isinstance(payload, dict) or set(payload) - {"font_id", "design", "time", *content_keys}:
            raise ValueError("제목·자막·컷·채널·맥락 문구와 글꼴·디자인 설정만 변경할 수 있습니다.")
        fid = payload.get("font_id", self.font_id)
        if not isinstance(fid, str) or fid not in self.fonts:
            raise ValueError("선택한 글꼴이 없습니다.")
        raw = copy.deepcopy(self.raw)
        raw.update(font=str(self.fonts[fid]["path"]), font_name=self.fonts[fid]["family"],
                   design=copy.deepcopy(payload.get("design", raw.get("design", {}))))
        raw.update({key: copy.deepcopy(payload[key]) for key in content_keys if key in payload})
        normalized = self._normalize(raw)
        # Fit text now so a saved plan can be rendered with these assets.
        preview_layout(normalized, Path(raw["font"]), 0)
        return raw, fid

    def layout(self, payload):
        """Validate an unsaved content edit and return its output timeline geometry."""
        with self.lock:
            raw, _ = self.edited(payload)
            normalized = self._normalize(raw)
            font = Path(raw["font"])
            normalized["design"] = self._resolved_design(normalized, font)
            return {"normalized": normalized, "duration": normalized["duration_seconds"],
                    "layout": preview_layout(normalized, font, payload.get("time", 0))}

    def save_edit(self, payload):
        with self.lock:
            raw, fid = self.edited(payload)
            raw[DRAFT_ORIGIN_KEY] = {"plan_path": str(self.plan_path)}
            self._protect_inputs([self.draft_path, self.draft_path.with_suffix(".json.tmp")], raw)
            save(self.draft_path, raw)
            self.raw, self.font_id = raw, fid
            return self.state()

    def _protect_inputs(self, destinations, raw):
        protected = [self.plan_path, *self.files.values(), Path(raw["font"]).resolve(), *self.protected_inputs]
        for destination in destinations:
            if destination.resolve() in protected or (destination.exists() and any(p.exists() and destination.samefile(p) for p in protected)):
                raise ValueError("출력 위치가 원본 편집안 또는 입력 파일과 겹칩니다. 다른 --output을 지정하세요.")

    def add_font(self, content, name):
        suffix = Path(name).suffix.lower()
        if suffix not in {".ttf", ".otf"} or not 0 < len(content) <= MAX_FONT_BYTES:
            raise ValueError("20MB 이하의 TTF 또는 OTF 파일을 선택하세요.")
        folder = self.output / "fonts"
        folder.mkdir(exist_ok=True)
        path = folder / (hashlib.sha256(content).hexdigest() + suffix)
        if not path.exists():
            path.write_bytes(content)
        try:
            with self.lock:
                fid = self._add_font(path)
                state = self.state()
                state["active_font"] = fid
                return state
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            raise ValueError("읽을 수 없는 글꼴 파일입니다.") from None

    def preview(self, payload):
        with self.lock:
            raw, _ = self.edited(payload)
        with self.preview_lock, tempfile.TemporaryDirectory(prefix="preview-", dir=self.output) as temporary:
            path = Path(temporary)
            save(path / "plan.json", raw)
            render_preview(path / "plan.json", path / "preview.png", payload.get("time", 0))
            return (path / "preview.png").read_bytes()

    def start_render(self, payload):
        with self.lock:
            if self.job["state"] == "running":
                raise ValueError("이미 영상을 만들고 있습니다. 완료 후 다시 시도하세요.")
            raw, _ = self.edited(payload)
            slug = raw.get("slug", "short")
            names = [f"{slug}.mp4", f"{slug}.ass", f"{slug}.ko.srt", f"{slug}.json", "manifest.json", "index.html"]
            self._protect_inputs([self.output / "renders" / name for name in names], raw)
            self.save_edit(payload)
            job_id = secrets.token_hex(8)
            request_path = self.output / "requests" / f"{job_id}.json"
            save(request_path, self.raw)
            self.job = {"state": "running", "job_id": job_id}

        def work():
            try:
                row = render_plan(request_path, self.output / "renders", gallery=True)
                result = {"state": "done", "job_id": job_id,
                          "url": f"/output/{quote(row['video'])}?token={self.token}",
                          "path": str(self.output / "renders" / row["video"])}
            except Exception as error:
                result = {"state": "error", "job_id": job_id, "error": str(error)}
            with self.lock:
                self.job = result
        threading.Thread(target=work, daemon=True).start()
        return {"job_id": job_id}


class EditorLibrary:
    """A local catalog with a separate draft and render session for every Short."""

    def __init__(self, library_path, output_dir):
        self.path = Path(library_path).resolve()
        if not self.path.exists():
            raise ValueError("쇼츠 목록 파일 또는 폴더를 찾을 수 없습니다.")
        self.output = Path(output_dir).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.entries, self.sessions = {}, {}
        self.warnings = []
        self.single = False
        self.primary_id = None
        self.refresh()

    @classmethod
    def from_plan(cls, plan_path, output_dir):
        # Preserve the original single-plan draft location for existing users.
        library = cls.__new__(cls)
        library.path = Path(plan_path).resolve()
        library.output = Path(output_dir).resolve()
        library.token = secrets.token_urlsafe(32)
        library.lock = threading.RLock()
        library.entries, library.sessions, library.warnings = {}, {}, []
        library.single = True
        item = library._entry({"plan": str(library.path)}, library.path.parent)
        library.primary_id = item["id"]
        library.entries[item["id"]] = item
        library.refresh()
        return library

    @staticmethod
    def _path(value, base):
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        return (base / path).resolve() if not path.is_absolute() else path.resolve()

    def _entry(self, row, base):
        plan_path = self._path(row.get("plan") or row.get("plan_file"), base)
        video = self._path(row.get("video"), base)
        identity = video or plan_path
        if identity is None:
            raise ValueError("목록에 영상 또는 편집안 경로가 필요합니다.")
        item_id = hashlib.sha256(str(identity).encode()).hexdigest()[:20]
        raw, reason = {}, ""
        if plan_path and plan_path.is_file():
            try:
                raw = read(plan_path)
                if not isinstance(raw, dict) or not raw.get("keep"):
                    raise ValueError("Invalid plan")
                for key in ("source", "font", "logo"):
                    asset = self._path(raw.get(key), plan_path.parent)
                    if asset is None or not asset.is_file():
                        reason = "원본 영상이나 편집에 필요한 파일을 찾을 수 없습니다."
                        break
            except (ValueError, OSError, TypeError):
                raw, reason = {}, "편집 정보를 읽을 수 없습니다."
        else:
            reason = "이 영상에 연결된 편집 정보가 없습니다."
        lines = raw.get("title_lines") or row.get("title_lines") or []
        title = row.get("title") or " ".join(str(x) for x in lines) or identity.stem
        try:
            duration = sum(float(end) - float(start) for start, end in raw.get("keep", []))
            duration = duration or float(row.get("actual_duration_seconds") or row.get("duration_seconds") or 0)
        except (ValueError, TypeError):
            duration = 0
        if not math.isfinite(duration) or duration < 0:
            duration = 0
        return {"id": item_id, "title": str(title), "title_lines": lines, "duration": duration,
                "video": video, "plan": plan_path, "editable": not bool(reason), "reason": reason,
                "slug": raw.get("slug", "short")}

    def refresh(self):
        with self.lock:
            entries, warnings = {}, []
            if self.single:
                item = self._entry({"plan": str(self.path)}, self.path.parent)
                entries[item["id"]] = item
                paths = []
            elif self.path.is_dir():
                paths = sorted(self.path.rglob("manifest.json"))
            else:
                paths = [self.path]
            imported = self.output / "imports" / "catalog.json"
            if imported.is_file() and imported not in paths:
                paths.append(imported)
            for path in paths:
                # Edits belong to their original library card, rather than duplicate cards.
                if path != imported and self.path.is_dir() and (path.is_relative_to(self.output / "items") or
                                                                path.is_relative_to(self.output / "imports")):
                    continue
                try:
                    catalog = read(path)
                    if not isinstance(catalog, dict):
                        raise ValueError("목록 형식이 올바르지 않습니다.")
                    rows = catalog.get("items", catalog.get("renders"))
                    if not isinstance(rows, list):
                        raise ValueError("items 또는 renders 목록이 필요합니다.")
                    for row in rows:
                        if not isinstance(row, dict):
                            raise ValueError("잘못된 쇼츠 항목입니다.")
                        item = self._entry(row, path.parent)
                        playable = item["video"] and item["video"].is_file() and item["video"].suffix.lower() == ".mp4"
                        if playable or (item["video"] is None and item["editable"]):
                            if path == imported and any(e["plan"] == item["plan"] for e in entries.values()):
                                continue
                            entries[item["id"]] = item
                except (ValueError, OSError, TypeError) as error:
                    if path == self.path and not self.path.is_dir():
                        raise ValueError(f"쇼츠 목록을 읽을 수 없습니다: {error}") from error
                    warnings.append(f"{path.name}: 지원하는 쇼츠 목록이 아니어서 건너뛰었습니다.")
            self.entries, self.warnings = entries, warnings

    def add_plan(self, plan_path):
        """Register a valid editable plan without modifying the supplied catalog."""
        plan_path = Path(plan_path).resolve()
        _, files, _ = load_render_plan(plan_path)
        with self.lock:
            self.refresh()
            existing = next((item for item in self.entries.values() if item["plan"] == plan_path), None)
            if existing:
                return dict(existing)
            catalog_path = self.output / "imports" / "catalog.json"
            protected = [plan_path, *files.values(), *self._protected_paths(include_import=False)]
            for destination in (catalog_path, catalog_path.with_suffix(".json.tmp")):
                if destination.resolve() in protected or (destination.exists() and any(
                        p.is_file() and destination.samefile(p) for p in protected)):
                    raise ValueError("등록 목록의 출력 위치가 원본 목록이나 입력 파일과 겹칩니다.")
            catalog = read(catalog_path) if catalog_path.exists() else {"schema_version": 1, "items": []}
            if not isinstance(catalog, dict) or not isinstance(catalog.get("items"), list):
                raise ValueError("가져온 편집안 목록의 형식이 올바르지 않습니다.")
            for row in catalog["items"]:
                if not isinstance(row, dict):
                    raise ValueError("가져온 편집안 목록에 잘못된 항목이 있습니다.")
                self._entry(row, catalog_path.parent)
            catalog["items"].append({"plan": str(plan_path)})
            save(catalog_path, catalog)
            self.refresh()
            item = next(item for item in self.entries.values() if item["plan"] == plan_path)
            for session in self.sessions.values():
                session.protected_inputs = self._protected_paths()
            return dict(item)

    def _output_for(self, item_id):
        return self.output if self.single and item_id == self.primary_id else self.output / "items" / item_id

    def _protected_paths(self, *, include_import=True):
        paths = [self.path]
        if include_import:
            paths.append(self.output / "imports" / "catalog.json")
        for entry in self.entries.values():
            paths.extend(p for p in (entry["plan"], entry["video"]) if p)
            if entry["plan"] and entry["plan"].is_file():
                try:
                    raw = read(entry["plan"])
                    paths.extend(path for key in ("source", "font", "logo")
                                 if (path := self._path(raw.get(key), entry["plan"].parent)))
                except (ValueError, OSError, TypeError, AttributeError):
                    pass
        return paths

    def get_entry(self, item_id):
        if not isinstance(item_id, str) or item_id not in self.entries:
            raise ValueError("목록에서 편집할 쇼츠를 선택하세요.")
        return self.entries[item_id]

    def get_session(self, item_id):
        with self.lock:
            entry = self.get_entry(item_id)
            if not entry["editable"]:
                raise ValueError(entry["reason"])
            if item_id not in self.sessions:
                output = self._output_for(item_id)
                session = EditorSession(entry["plan"], output)
                session.token = self.token
                session.protected_inputs = self._protected_paths()
                self.sessions[item_id] = session
            return self.sessions[item_id]

    def video_path(self, item_id):
        entry = self.get_entry(item_id)
        output = self._output_for(item_id)
        # Only a renderer-produced manifest can select a replacement preview.
        manifest = output / "renders" / "manifest.json"
        if manifest.is_file():
            try:
                rows = read(manifest).get("renders", [])
                for row in rows:
                    name = row.get("video", "")
                    if row.get("slug") == entry["slug"] and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}\.mp4", name):
                        candidate = manifest.parent / name
                        if candidate.is_file():
                            return candidate
            except (ValueError, OSError, TypeError, AttributeError):
                pass
        return entry["video"]

    def list_items(self):
        self.refresh()
        with self.lock:
            items = []
            for entry in self.entries.values():
                video = self.video_path(entry["id"])
                items.append({k: entry[k] for k in ("id", "title", "title_lines", "duration", "editable", "reason")})
                draft = self._output_for(entry["id"]) / "edit-plan.json"
                try:
                    if entry["id"] in self.sessions:
                        with self.sessions[entry["id"]].lock:
                            current = copy.deepcopy(self.sessions[entry["id"]].raw)
                    elif draft.is_file() and entry["plan"]:
                        current = read(draft)
                        original = read(entry["plan"])
                        _check_draft_origin(current, original, entry["plan"])
                        if (self._path(current.get("source"), draft.parent) != self._path(original.get("source"), entry["plan"].parent)
                                or current.get("source_start", 0) != original.get("source_start", 0)
                                or current.get("slug", "short") != original.get("slug", "short")):
                            raise ValueError("다른 원본의 편집안입니다.")
                    else:
                        current = None
                    if current is not None:
                        lines = current["title_lines"]
                        duration = sum(float(end) - float(start) for start, end in current["keep"])
                        if (not isinstance(lines, list) or not 1 <= len(lines) <= 2 or
                                not all(isinstance(line, str) and line.strip() for line in lines) or
                                not math.isfinite(duration) or duration <= 0):
                            raise ValueError("저장된 편집안의 제목이나 길이가 올바르지 않습니다.")
                        items[-1].update(title=" ".join(lines), title_lines=lines, duration=duration)
                except (ValueError, OSError, TypeError, KeyError, AttributeError):
                    pass  # The editor reports an invalid saved draft when the user opens it.
                items[-1]["video_url"] = (f"/media/rendered?item={entry['id']}&token={self.token}"
                                          if video and video.is_file() else None)
            return {"items": items, "warnings": self.warnings}


def make_server(owner, port=0, *, workspace=None):
    library = owner if isinstance(owner, EditorLibrary) else None
    if library:
        from .pipeline import PipelineManager
        manager = PipelineManager(library, workspace=Path(workspace or Path.cwd()))
    else:
        manager = None

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass  # Media URLs carry the session token; never log them.

        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # Browser seeks/reloads routinely cancel keep-alive connections.

        def _url(self):
            return urlsplit(self.path)

        def _authorized(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            if host not in allowed or (origin and origin not in {"http://" + h for h in allowed}):
                return False
            token = self.headers.get("X-Editor-Token") or parse_qs(self._url().query).get("token", [""])[0]
            return secrets.compare_digest(token, owner.token)

        def _item(self):
            return parse_qs(self._url().query).get("item", [""])[0]

        def _session(self):
            return library.get_session(self._item()) if library else owner

        def _manager(self):
            if manager is None:
                raise ValueError("프로젝트 작업에는 쇼츠 목록 모드가 필요합니다.")
            return manager

        def _state(self, value):
            if library:
                value["item_id"] = self._item()
            return value

        def _send(self, data, content_type="application/json; charset=utf-8", status=200):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _file(self, path):
            size = path.stat().st_size
            try:
                start, end, partial = byte_range(self.headers.get("Range"), size)
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(max(0, end - start + 1)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                with path.open("rb") as stream:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        data = stream.read(min(256 * 1024, remaining))
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = self._url()
            if url.path == "/" and library:
                return self._send(Path(__file__).with_name("library_ui.html").read_bytes(), "text/html; charset=utf-8")
            if url.path in {"/", "/editor"}:
                return self._send(Path(__file__).with_name("editor_ui.html").read_bytes(), "text/html; charset=utf-8")
            if not self._authorized():
                return self._send({"error": "편집기를 실행할 때 표시된 주소로 접속하세요."}, status=403)
            try:
                if url.path == "/api/library" and library:
                    return self._send(library.list_items())
                query = parse_qs(url.query)
                if url.path == "/api/projects":
                    return self._send(self._manager().list_projects())
                if url.path == "/api/project":
                    return self._send(self._manager().detail(query.get("id", [""])[0]))
                if url.path == "/api/project/file":
                    return self._file(self._manager().file_path(query.get("id", [""])[0], query.get("name", [""])[0]))
                if url.path == "/media/rendered" and library:
                    path = library.video_path(self._item())
                    if path and path.is_file():
                        return self._file(path)
                    return self._send({"error": "생성된 영상이 아직 없습니다."}, status=404)
                session = self._session()
                if url.path == "/api/state":
                    return self._send(self._state(session.state()))
                if url.path == "/api/render-status":
                    with session.lock:
                        job = dict(session.job)
                        if library and job.get("url"):
                            job["url"] += "&item=" + self._item()
                        return self._send(job)
                if url.path in {"/media/source", "/media/logo"}:
                    return self._file(session.files[url.path.rsplit("/", 1)[1]])
                if url.path == "/media/font":
                    fid = parse_qs(url.query).get("id", [""])[0]
                    if fid in session.fonts:
                        return self._file(session.fonts[fid]["path"])
                if url.path.startswith("/output/"):
                    name = url.path.removeprefix("/output/")
                    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}\.(?:mp4|ass|ko\.srt)", name):
                        path = session.output / "renders" / name
                        if path.is_file():
                            return self._file(path)
                return self._send({"error": "파일을 찾을 수 없습니다."}, status=404)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
                return self._send({"error": str(error)}, status=400)

        def do_POST(self):
            if not self._authorized():
                self.close_connection = True
                return self._send({"error": "편집기 세션 인증에 실패했습니다."}, status=403)
            try:
                url = self._url()
                path, query = url.path, parse_qs(url.query)
                project_paths = {"/api/projects", "/api/project/action"}
                if path not in {"/api/font", "/api/save", "/api/preview", "/api/render", "/api/layout", "/api/assets", *project_paths}:
                    self.close_connection = True
                    return self._send({"error": "지원하지 않는 요청입니다."}, status=404)
                length = int(self.headers.get("Content-Length", "0"))
                if path == "/api/assets":
                    return self._send(self._manager().upload(self.rfile, length, query.get("kind", [""])[0],
                                                            query.get("name", [""])[0]))
                maximum = MAX_FONT_BYTES if path == "/api/font" else MAX_PROJECT_BYTES if path in project_paths else MAX_EDIT_BYTES
                if not 0 < length <= maximum:
                    self.close_connection = True
                    raise ValueError("요청 파일이 비어 있거나 너무 큽니다.")
                content = self.rfile.read(length)
                if path in project_paths:
                    payload = json.loads(content)
                    if path == "/api/projects":
                        return self._send(self._manager().create(payload))
                    return self._send(self._manager().action(query.get("id", [""])[0], payload))
                session = self._session()
                if path == "/api/font":
                    name = parse_qs(self._url().query).get("name", [""])[0]
                    return self._send(self._state(session.add_font(content, name)))
                payload = json.loads(content)
                if path == "/api/save":
                    return self._send(self._state(session.save_edit(payload)))
                if path == "/api/layout":
                    return self._send(session.layout(payload))
                if path == "/api/preview":
                    return self._send(session.preview(payload), "image/png")
                if path == "/api/render":
                    return self._send(session.start_render(payload), status=202)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
                self.close_connection = True
                return self._send({"error": str(error)}, status=400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.pipeline_manager = manager
    return server


def serve_editor(plan_path=None, output_dir=None, *, library_path=None, port=0, open_browser=True, workspace=None):
    if bool(plan_path) == bool(library_path):
        raise ValueError("--plan 또는 --library 중 하나를 지정하세요.")
    path = Path(library_path or plan_path).resolve()
    output_dir = Path(output_dir).resolve() if output_dir else (path / "editor" if path.is_dir() else path.parent / (path.stem + "-editor"))
    library = EditorLibrary(path, output_dir) if library_path else EditorLibrary.from_plan(path, output_dir)
    server = make_server(library, port, workspace=workspace)
    url = f"http://127.0.0.1:{server.server_port}/#token={library.token}"
    print(f"내 쇼츠 목록: {url}", flush=True)
    print(f"편집안 저장 폴더: {output_dir}\n종료하려면 Ctrl+C를 누르세요.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server.pipeline_manager is not None:
            server.pipeline_manager.close()
        server.server_close()
