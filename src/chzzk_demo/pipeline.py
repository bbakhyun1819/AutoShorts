"""Local studio workflow. Existing CLI stages remain the source of truth.

Preparing an assistant packet is a waiting state, never an AI invocation. Source
files and imported jobs are not replaced by new projects or edit drafts.
"""
from __future__ import annotations

import bisect
import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from .core import read, save

STEPS = [("ingest", "원본 수집"), ("chats", "채팅 수집"), ("transcribe", "음성 전사"),
         ("sync", "싱크 확인"), ("signals", "반응 신호"), ("analysis", "내용 분석"),
         ("review", "후보 검토"), ("edit", "편집안"), ("render", "영상 출력")]
DEFAULTS = {"channel": "", "logo": "", "font": "", "font_name": "맑은 고딕", "model": "large-v3",
            "device": "cuda", "compute": "float16", "batch_size": 8, "provider": "assistant",
            "quality": "1080", "review_limit": 10, "frames": 6,
            "min_length": 60, "max_length": 110, "render_count": 1}
ASSET_RULES = {"video": ({".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts"}, 50 * 1024**3),
               "chat": ({".json"}, 100 * 1024**2), "response": ({".json"}, 10 * 1024**2),
               "logo": ({".png", ".jpg", ".jpeg", ".webp"}, 20 * 1024**2),
               "font": ({".ttf", ".otf"}, 20 * 1024**2)}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def number(value, label, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: 유한한 숫자를 입력하세요.")
    if not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f"{label}: {low}~{high} 범위{'의 정수' if integer else ''}를 입력하세요.")
    return int(value) if integer else float(value)


def text(value, label, maximum=6000):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{label}: {maximum}자 이하의 문자열을 입력하세요.")
    return value.strip()


def grouped_captions(words, start, end):
    """Make editable, non-overlapping caption drafts from existing ASR words."""
    result, group = [], []
    previous = start

    def emit():
        if group:
            value = "".join(w[2] for w in group).strip()
            if value:
                result.append({"start": group[0][0], "end": group[-1][1], "text": value, "style": "Caption"})
            group.clear()

    for word in sorted(words, key=lambda w: w["start"]):
        a, b = max(start, previous, float(word["start"])), min(end, float(word["end"]))
        value = str(word.get("text", "")).replace("\n", " ").replace("\r", " ")
        if b <= a or not value.strip():
            continue
        if group and (a - group[-1][1] > .65 or b - group[0][0] > 3.5 or
                      len("".join(w[2] for w in group)) + len(value) > 28):
            emit()
        group.append((a, b, value))
        previous = b
        if value.rstrip().endswith((".", "?", "!")):
            emit()
    emit()
    return result


class PipelineManager:
    def __init__(self, library, workspace=None):
        self.library = library
        self.workspace = Path(workspace or Path.cwd()).resolve()
        self.root = self.workspace / "runs" / "studio"
        self.root.mkdir(parents=True, exist_ok=True)
        self.projects_root = self.root / "projects"
        self.projects_root.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.projects, self.cache, self.processes = {}, {}, {}
        self.closing = False
        self.workers = set()
        self.active = None
        self.defaults = copy.deepcopy(DEFAULTS)
        # Reuse the user's existing approved assets without copying source media.
        if library:
            for item in library.entries.values():
                plan = item.get("plan")
                if plan and plan.is_file():
                    try:
                        raw = read(plan)
                        for key in ("font", "logo"):
                            value = Path(raw[key])
                            self.defaults[key] = str((plan.parent / value).resolve() if not value.is_absolute() else value)
                        self.defaults.update({k: raw[k] for k in ("channel", "font_name") if k in raw})
                        break
                    except (ValueError, KeyError, OSError, TypeError):
                        continue
        if not self.defaults["font"]:
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgunbd.ttf"
            if font.is_file():
                self.defaults["font"] = str(font)
        self.warnings = []
        settings = self.root / "settings.json"
        if settings.is_file():
            try:
                stored = read(settings)
                for key in ("font", "logo"):
                    if stored.get(key) and not Path(stored[key]).is_file():
                        self.warnings.append(f"저장된 {key} 파일이 없어 기본 파일을 사용합니다. 설정에서 다시 선택하세요.")
                        stored.pop(key)
                merged = {**self.defaults, **stored}
                self.defaults.update(self._validate_settings(merged, defaults=True))
            except (ValueError, OSError, TypeError, AttributeError) as error:
                self.warnings.append("기본 설정을 복원하지 못했습니다: " + str(error))
        for path in sorted(self.projects_root.glob("*/project.json")):
            try:
                data = read(path)
                if data.get("id") != path.parent.name or not re.fullmatch(r"[a-f0-9]{16}", data["id"]):
                    continue
                if data.get("task", {}).get("state") == "running":
                    data["task"].update(state="interrupted", error="서버가 종료되었습니다. 해당 단계를 다시 실행하세요.")
                    data.update(state="interrupted", error=data["task"]["error"])
                    for step in data.get("steps", []):
                        if step["state"] == "running":
                            step["state"] = "interrupted"
                self.projects[data["id"]] = data
                self._persist(data)
                if library:
                    for plan in data.get("plans", []):
                        if Path(plan["path"]).is_file():
                            plan["item_id"] = library.add_plan(plan["path"])["id"]
            except (ValueError, OSError, KeyError, TypeError):
                continue

    def _json(self, path, default=None):
        path = Path(path)
        if not path.is_file():
            return default
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        cached = self.cache.get(path)
        if not cached or cached[0] != stamp:
            cached = (stamp, read(path))
            self.cache[path] = cached
        return cached[1]

    def _persist(self, project):
        project["updated"] = now()
        save(self.projects_root / project["id"] / "project.json", project)

    def _get(self, project_id):
        if not isinstance(project_id, str) or project_id not in self.projects:
            raise ValueError("프로젝트를 선택하세요.")
        return self.projects[project_id]

    def _file_value(self, value, label, required=False):
        value = text(value, label, 4096)
        if not value:
            if required:
                raise ValueError(f"{label} 파일을 선택하세요.")
            return ""
        if "://" in value:
            raise ValueError(f"{label}: 로컬 파일 경로를 입력하세요.")
        path = Path(value)
        path = (self.workspace / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file():
            raise ValueError(f"{label} 파일을 찾을 수 없습니다: {path}")
        return str(path)

    def _validate_settings(self, payload, defaults=False):
        if not isinstance(payload, dict):
            raise ValueError("설정은 JSON 객체여야 합니다.")
        allowed = set(DEFAULTS) | {"request", "title_override", "caption_notes", "reference", "allow_no_chat"}
        if set(payload) - allowed:
            raise ValueError("지원하지 않는 설정: " + ", ".join(sorted(set(payload) - allowed)))
        out = {}
        for key, value in payload.items():
            if key in {"logo", "font"}:
                out[key] = self._file_value(value, key)
            elif key in {"batch_size", "review_limit", "frames", "min_length", "max_length", "render_count"}:
                bounds = {"batch_size": (1, 64), "review_limit": (1, 100), "frames": (0, 24),
                          "min_length": (10, 180), "max_length": (10, 180), "render_count": (1, 20)}[key]
                out[key] = number(value, key, *bounds, integer=True)
            elif key == "allow_no_chat":
                if not isinstance(value, bool):
                    raise ValueError("채팅 생략은 true/false여야 합니다.")
                out[key] = value
            else:
                out[key] = text(value, key, 6000 if key in {"request", "caption_notes"} else 500)
        for key, choices in {"provider": {"assistant", "gemini"}, "device": {"cuda", "cpu"},
                             "compute": {"float16", "float32", "int8_float16", "int8"},
                             "quality": {"best", "480", "720", "1080", "best[height<=480]", "best[height<=720]", "best[height<=1080]"}}.items():
            if key in out and out[key] not in choices:
                raise ValueError(f"{key}: 지원하지 않는 값입니다.")
        if "model" in out and not re.fullmatch(r"[A-Za-z0-9_.\-/]{1,160}", out["model"]):
            raise ValueError("올바른 전사 모델 이름을 입력하세요.")
        if out.get("min_length", 10) > out.get("max_length", 180):
            raise ValueError("최소 길이는 최대 길이보다 길 수 없습니다.")
        return out

    def list_projects(self):
        with self.lock:
            rows = []
            for value in sorted(self.projects.values(), key=lambda p: p["updated"], reverse=True):
                row = {k: copy.deepcopy(value.get(k)) for k in ("id", "title", "state", "stage", "job_path", "updated", "error")}
                row.update({k: value["config"].get(k) for k in ("start", "end", "provider")})
                rows.append(row)
            return {"projects": rows, "defaults": copy.deepcopy(self.defaults), "warnings": list(self.warnings)}

    def create(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("프로젝트 정보가 필요합니다.")
        with self.lock:
            imported = payload.get("job_path")
            config = {**self.defaults, "request": "", "allow_no_chat": False, "start": 0, "end": 7200,
                      "url": "", "local_video": "", "chat_path": "", "cookies_path": ""}
            special = {"title", "job_path", "url", "local_video", "start", "end", "chat_path", "cookies_path"}
            config.update(self._validate_settings({k: v for k, v in payload.items() if k not in special}))
            if config["min_length"] > config["max_length"]:
                raise ValueError("최소 길이는 최대 길이보다 길 수 없습니다.")
            project_id = uuid.uuid4().hex[:16]
            job = self.projects_root / project_id / "job"
            if imported:
                supplied = Path(text(imported, "작업 폴더", 4096))
                job = (self.workspace / supplied).resolve() if not supplied.is_absolute() else supplied.resolve()
                if not (job / "job.json").is_file():
                    raise ValueError("job.json이 있는 기존 작업 폴더를 지정하세요.")
                for project in self.projects.values():
                    if Path(project["job_path"]) == job:
                        return self.detail(project["id"])
                source = read(job / "job.json")
                self._file_value(source.get("video", ""), "원본 영상", True)
                config.update(start=source["start"], end=source["end"], url=source.get("url") or "",
                              local_video=source["video"] if source.get("video_id") == "local" else "")
            else:
                for key in ("local_video", "chat_path", "cookies_path"):
                    config[key] = self._file_value(payload.get(key, ""), key)
                config["url"] = text(payload.get("url", ""), "다시보기 주소", 2000)
                if bool(config["url"]) == bool(config["local_video"]):
                    raise ValueError("다시보기 링크 또는 로컬 영상 중 하나를 선택하세요.")
                if config["url"] and not re.fullmatch(r"https://chzzk\.naver\.com/video/\d+/?(?:\?.*)?", config["url"]):
                    raise ValueError("치지직 다시보기 주소를 입력하세요.")
                config["start"] = number(payload.get("start", 0), "시작 시각", 0, 864000, True)
                config["end"] = number(payload.get("end", 7200), "끝 시각", 1, 864000, True)
                if config["end"] <= config["start"]:
                    raise ValueError("끝 시각은 시작 시각보다 뒤여야 합니다.")
                if config["local_video"] and not config["chat_path"] and not config["allow_no_chat"]:
                    raise ValueError("로컬 영상은 채팅 파일을 선택하거나 ‘채팅 없이 분석’을 선택하세요.")
            value = {"id": project_id, "title": text(payload.get("title") or job.name, "프로젝트 이름", 160),
                     "job_path": str(job), "config": config, "state": "ready", "stage": "ingest", "error": None,
                     "task": {"state": "idle"}, "steps": [{"key": k, "label": label, "state": "pending"} for k, label in STEPS],
                     "packets": {}, "plans": [], "logs": [], "created": now(), "updated": now()}
            if imported:
                for key, filename in {"ingest": "job.json", "chats": "chats.json", "transcribe": "transcript.json",
                                      "signals": "signal-candidates.json", "analysis": "text-candidates.json"}.items():
                    if (job / filename).is_file():
                        self._step(value, key, "done", persist=False)
                if source.get("sync_status") == "confirmed":
                    self._step(value, "sync", "done", persist=False)
                value["state"] = "candidates_ready" if (job / "candidates.json").is_file() else "ready"
            self.projects[project_id] = value
            self._persist(value)
            return self.detail(project_id)

    def upload(self, stream, length, kind, name):
        suffix = Path(name or "").suffix.lower()
        if kind not in ASSET_RULES or suffix not in ASSET_RULES[kind][0]:
            raise ValueError("지원하지 않는 파일 형식입니다.")
        if not 0 < length <= ASSET_RULES[kind][1]:
            raise ValueError("파일이 비어 있거나 업로드 상한을 넘었습니다.")
        directory = self.root / "uploads"
        directory.mkdir(exist_ok=True)
        target = directory / (uuid.uuid4().hex + suffix)
        temporary = target.with_suffix(suffix + ".part")
        extra = {}
        try:
            with temporary.open("xb") as out:
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024**2, remaining))
                    if not chunk:
                        raise ValueError("업로드가 완료되지 않았습니다.")
                    out.write(chunk)
                    remaining -= len(chunk)
            if kind in {"chat", "response"}:
                read(temporary)
            if kind == "logo":
                from PIL import Image
                with Image.open(temporary) as img:
                    img.verify()
            if kind == "font":
                from PIL import ImageFont
                extra["font_name"] = ImageFont.truetype(str(temporary), 24).getname()[0]
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"path": str(target), "name": Path(name).name, **extra}

    def _url(self, project, name):
        return "/api/project/file?" + urlencode({"id": project["id"], "name": name, "token": self.library.token})

    def _packet(self, project, stage):
        folder = project.get("packets", {}).get(stage, {}).get("path")
        if folder and (Path(folder) / "packet.json").is_file():
            return Path(folder)
        paths = list((Path(project["job_path"]) / "assistant" / stage).glob("*/packet.json"))
        return max(paths, key=lambda p: p.stat().st_mtime_ns).parent if paths else None

    def _request(self, project, stage, folder):
        c = project["config"]
        return (f"작업 폴더: {project['job_path']}\n분석 자료: {folder}\n"
                f"이 저장소의 AGENTS.md와 자료의 INSTRUCTIONS.md를 따라 {stage} 단계를 검토해 주세요.\n"
                "자료를 준비한 것만으로 분석을 완료했다고 표시하지 마세요. 실제로 확인한 근거와 한계를 기록하세요.\n"
                f"사용자 요청: {c.get('request') or '도입부터 마지막 반응까지 이야기가 완결되는 후보를 찾아주세요.'}\n"
                f"희망 길이: {c['min_length']}~{c['max_length']}초. 결말을 우선하고 길이를 억지로 채우지 마세요.\n"
                f"정밀 검토 후보 수: {c['review_limit']}개, 우선 제작 수: {c['render_count']}개. 전체 후보 목록은 보존하세요.\n"
                f"추가 자막 요청: {c.get('caption_notes', '')}\n참고: {c.get('reference', '')}\n"
                + ("이번 요청은 채팅 없이 분석합니다. 자료에 기존 채팅이 있어도 판단 근거에서 제외하세요.\n" if c['allow_no_chat'] else "") +
                "response-template.json의 식별값을 유지하여 response.json을 작성해 주세요. "
                "완료한 결과는 작업실의 ‘결과 JSON 가져오기’로 입력합니다.\n")

    def detail(self, project_id):
        with self.lock:
            project = self._get(project_id)
            result = copy.deepcopy(project)
            job = Path(project["job_path"])
            config = self._json(job / "job.json", {})
            result.update({k: project["config"].get(k) for k in ("start", "end", "provider")})
            result["source_url"] = self._url(project, "source") if config.get("video") else None
            result["sync_offset"] = config.get("chat_offset", 0)
            result["sync_confirmed"] = config.get("sync_status") == "confirmed"
            result["files"] = []
            for name, label in [("transcript.json", "전체 대본"), ("candidates.json", "전체 후보"),
                                ("text-analysis.json", "분석 결과 정보")]:
                if (job / name).is_file():
                    result["files"].append({"name": name, "label": label, "url": self._url(project, name)})
            result["packets"] = {}
            for stage in ("text", "review"):
                folder = self._packet(project, stage)
                if folder:
                    result["packets"][stage] = {"path": str(folder), "request": self._request(project, stage, folder),
                                                "template_url": self._url(project, stage + "-template")}
            candidates = self._json(job / "candidates.json", [])
            words = self._json(job / "transcript.json", [])
            word_starts = [w["start"] for w in words]
            reviews = {}
            if candidates and words:
                try:
                    from .report import applicable_reviews
                    reviews = applicable_reviews(job, candidates)
                except (ValueError, OSError, KeyError):
                    pass
            result["candidates"] = []
            for row in candidates:
                item = copy.deepcopy(row)
                clip = job / "clips" / (item["id"] + ".mp4")
                item.update(video_url=self._url(project, "clip:" + item["id"]) if clip.is_file() else result["source_url"],
                            preview_start=0 if clip.is_file() else item["start"],
                            preview_end=item["end"] - item["start"] if clip.is_file() else item["end"],
                            review=reviews.get(item["id"]))
                lo, hi = bisect.bisect_left(word_starts, item["start"]), bisect.bisect_left(word_starts, item["end"])
                item["transcript"] = "".join(w.get("text", "") for w in words[max(0, lo-1):hi] if w["end"] > item["start"])[:6000]
                result["candidates"].append(item)
            chats = self._json(job / "chats.json", [])
            result["sync_samples"] = []
            for sample in self._json(job / "sync-check" / "candidates.json", []):
                path = job / "sync-check" / "clips" / (sample["id"] + ".mp4")
                if path.is_file():
                    delta = config.get("chat_offset", 0)
                    result["sync_samples"].append({"id": sample["id"], "start": sample["start"], "end": sample["end"],
                        "video_url": self._url(project, sample["id"]), "chat": [m for m in chats if sample["start"]-120 <= m["time"]+delta <= sample["end"]+120]})
            items = {r["id"]: r for r in self.library.list_items()["items"]} if self.library else {}
            result["item_ids"] = [p["item_id"] for p in project.get("plans", [])]
            result["items"] = [items[p] for p in result["item_ids"] if p in items]
            result["assets"] = {k: project["config"].get(k, "") for k in ("font", "logo", "font_name", "channel")}
            return result

    def file_path(self, project_id, name):
        with self.lock:
            project = self._get(project_id)
            job = Path(project["job_path"])
            if name == "source":
                path = Path(read(job / "job.json")["video"])
            elif name in {"transcript.json", "candidates.json", "text-analysis.json"}:
                path = job / name
            elif name in {"text-template", "review-template"}:
                folder = self._packet(project, name.split("-")[0])
                if not folder:
                    raise ValueError("먼저 분석 자료를 준비하세요.")
                path = folder / "response-template.json"
            elif re.fullmatch(r"sync-[0-2]", name):
                path = job / "sync-check" / "clips" / (name + ".mp4")
            elif name.startswith("clip:") and name[5:] in {r["id"] for r in self._json(job / "candidates.json", [])}:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name[5:]):
                    raise ValueError("올바르지 않은 후보 ID입니다.")
                path = job / "clips" / (name[5:] + ".mp4")
            else:
                raise ValueError("제공하지 않는 프로젝트 파일입니다.")
            if not path.is_file():
                raise ValueError("아직 생성되지 않은 파일입니다.")
            return path

    def _step(self, project, key, state, persist=True):
        with self.lock:
            for step in project["steps"]:
                if step["key"] == key:
                    step["state"] = state
            project["stage"] = key
            if persist:
                self._persist(project)

    def _log(self, project, message):
        with self.lock:
            project["logs"].append({"time": now(), "message": str(message)[-1200:]})
            project["logs"] = project["logs"][-120:]
            self._persist(project)

    def _environment(self):
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["AUTOSHORTS_STUDIO_WORKER"] = "1"
        source_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
        if not shutil.which("ffmpeg"):
            for path in (self.workspace / "runs" / "tools" / "ffmpeg").glob("*/bin/ffmpeg.exe"):
                if path.with_name("ffprobe.exe").is_file():
                    env["PATH"] = str(path.parent) + os.pathsep + env.get("PATH", "")
                    break
        return env

    def _run(self, project, step, command, *args):
        self._step(project, step, "running")
        self._log(project, dict(STEPS)[step] + " 시작")
        cmd = [sys.executable, "-u", "-m", "chzzk_demo.cli", command, "--job", project["job_path"], *map(str, args)]
        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with self.lock:
            if self.closing:
                raise RuntimeError("서버가 종료되었습니다. 단계를 다시 실행하세요.")
            process = subprocess.Popen(cmd, cwd=self.workspace, env=self._environment(), stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                       creationflags=creation, start_new_session=os.name != "nt")
            self.processes[project["id"]] = process
        try:
            with process:
                tail = []
                for line in process.stdout:
                    if line.strip():
                        tail.append(line.strip())
                        tail = tail[-8:]
                        self._log(project, line.strip())
                code = process.wait()
                if code:
                    raise ValueError("\n".join(tail) or f"{command} 단계 실패 ({code})")
        finally:
            with self.lock:
                self.processes.pop(project["id"], None)
        self._step(project, step, "done")

    def close(self):
        """Stop owned CLI process trees before allowing a server restart."""
        with self.lock:
            self.closing = True
            processes, workers = list(self.processes.values()), list(self.workers)
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=False)
                else:
                    import signal
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        for worker in workers:
            worker.join(timeout=3)

    def _start(self, project, action, work):
        with self.lock:
            if self.closing:
                raise ValueError("서버가 종료 중입니다.")
            if self.active is not None:
                raise ValueError("진행 중인 제작 단계가 있습니다. 완료 후 다시 실행하세요.")
            self.active = project["id"]
            project.update(state="running", error=None, task={"state": "running", "action": action, "started": now()})
            self._persist(project)

        def run():
            try:
                work()
                with self.lock:
                    project["task"].update(state="done", finished=now())
                    if project["state"] == "running":
                        project["state"] = "ready"
            except Exception as error:
                with self.lock:
                    state = "interrupted" if self.closing else "error"
                    message = "서버가 종료되었습니다. 해당 단계를 다시 실행하세요." if self.closing else str(error)
                    project.update(state=state, error=message)
                    project["task"].update(state=state, error=message, finished=now())
                    for step in project["steps"]:
                        if step["state"] == "running":
                            step["state"] = state
                self._log(project, "실패: " + str(error))
            finally:
                with self.lock:
                    self.active = None
                    self._persist(project)
                    self.workers.discard(threading.current_thread())
        worker = threading.Thread(target=run, daemon=True)
        with self.lock:
            self.workers.add(worker)
            worker.start()
        return {"id": project["id"], "task": {"state": "running", "action": action}}

    def _write_request(self, project):
        c = project["config"]
        save(Path(project["job_path"]) / "studio-request.json",
             {k: c.get(k, "") for k in ("request", "min_length", "max_length", "caption_notes", "reference", "allow_no_chat")})

    def _prepare(self, project):
        c, job = project["config"], Path(project["job_path"])
        if not (job / "job.json").is_file():
            source = ["--local-video", c["local_video"]] if c["local_video"] else ["--url", c["url"]]
            quality = c["quality"] if c["quality"].startswith("best") else "best[height<=" + c["quality"] + "]"
            options = ["--start", c["start"], "--duration", c["end"] - c["start"], "--format", quality]
            if c.get("cookies_path"):
                options += ["--cookies", c["cookies_path"]]
            self._run(project, "ingest", "ingest", *source, *options)
        else:
            self._step(project, "ingest", "done")
        actual = read(job / "job.json")
        previous_chats = self._json(job / "chats.json")
        c.update(start=actual["start"], end=actual["end"])
        if c["allow_no_chat"]:
            self._step(project, "chats", "skipped")
            self._step(project, "sync", "skipped")
        elif c.get("chat_path"):
            messages = read(c["chat_path"])
            if not isinstance(messages, list):
                raise ValueError("채팅 JSON은 time(원본 초), text를 가진 배열이어야 합니다.")
            normalized = []
            for i, message in enumerate(messages):
                if not isinstance(message, dict):
                    raise ValueError("채팅 항목은 객체여야 합니다.")
                t = number(message.get("time"), "채팅 시각", 0, actual["source_duration"] + 86400)
                normalized.append({"id": str(message.get("id", i)), "time": t, "text": text(message.get("text"), "채팅", 10000)})
            save(job / "chats.json", sorted(normalized, key=lambda m: m["time"]))
            save(job / "chat-status.json", {"status": "imported_unverified", "count": len(normalized)})
            self._step(project, "chats", "done")
        elif not (job / "chats.json").is_file() or self._json(job / "chat-status.json", {}).get("status") == "failed":
            options = ["--cookies", c["cookies_path"]] if c.get("cookies_path") else []
            self._run(project, "chats", "chats", *options)
        else:
            self._step(project, "chats", "done")
        if not c["allow_no_chat"] and self._json(job / "chats.json") != previous_chats:
            actual["sync_status"] = "unverified"
            save(job / "job.json", actual)
            for key in ("signals", "analysis", "review"):
                self._step(project, key, "pending", persist=False)
            self._log(project, "채팅 자료가 변경되었습니다. 영상과 채팅의 싱크를 다시 확인하세요.")
        if (job / "transcript.json").is_file():
            self._step(project, "transcribe", "done")
            self._log(project, "기존 대본을 재사용합니다.")
        else:
            self._run(project, "transcribe", "transcribe", "--model", c["model"], "--device", c["device"],
                      "--compute", c["compute"], "--batch-size", c["batch_size"])
        if not c["allow_no_chat"]:
            self._run(project, "sync", "sync-report")
            confirmed = actual.get("sync_status") == "confirmed"
            self._step(project, "sync", "done" if confirmed else "waiting")
            project["state"] = "ready_for_analysis" if confirmed else "waiting_sync"
        else:
            project["state"] = "ready_for_analysis"

    def _analyze(self, project):
        c, job = project["config"], Path(project["job_path"])
        actual = read(job / "job.json")
        if not (job / "transcript.json").is_file():
            raise ValueError("먼저 원본과 음성 전사를 준비하세요.")
        if not c["allow_no_chat"] and actual.get("sync_status") != "confirmed":
            raise ValueError("영상과 채팅의 싱크를 직접 확인한 뒤 분석을 시작하세요.")
        self._write_request(project)
        self._run(project, "signals", "signals", *(["--allow-no-chat"] if c["allow_no_chat"] else []))
        if c["provider"] == "assistant":
            self._run(project, "analysis", "assistant-prepare", "--stage", "text")
            folder = self._packet(project, "text")
            # Select the newly prepared packet rather than a prior recorded path.
            paths = list((job / "assistant" / "text").glob("*/packet.json"))
            folder = max(paths, key=lambda p: p.stat().st_mtime_ns).parent
            project["packets"]["text"] = {"path": str(folder)}
            self._step(project, "analysis", "waiting")
            project["state"] = "waiting_analysis"
        else:
            self._run(project, "analysis", "text-candidates")
            self._combine(project)

    def _combine(self, project):
        self._run(project, "analysis", "combine")
        self._run(project, "analysis", "report")
        rows = read(Path(project["job_path"]) / "candidates.json")
        project["state"] = "candidates_ready" if rows else "no_candidates"

    def _import_response(self, project, payload, stage):
        response = payload.get("response")
        if not isinstance(response, dict) or response.get("stage") != stage:
            raise ValueError(f"{stage} 단계의 분석 결과 JSON이 필요합니다.")
        path = self.projects_root / project["id"] / "responses" / (uuid.uuid4().hex + ".json")
        save(path, response)
        self._run(project, "analysis" if stage == "text" else "review", "assistant-import", "--input", path)
        if stage == "text":
            self._combine(project)
        else:
            self._run(project, "review", "report")
            project["state"] = "review_ready"

    def _review(self, project, payload):
        c, job = project["config"], Path(project["job_path"])
        candidates = read(job / "candidates.json")
        if not candidates:
            project["state"] = "no_candidates"
            self._step(project, "review", "skipped")
            return
        ids = payload.get("candidate_ids", [r["id"] for r in candidates[:c["review_limit"]]])
        if (not isinstance(ids, list) or not ids or len(ids) > 100 or any(not isinstance(i, str) for i in ids)
                or len(ids) != len(set(ids)) or set(ids) - {r["id"] for r in candidates}):
            raise ValueError("목록에 있는 후보를 중복 없이 선택하세요.")
        options = ["--limit", len(ids)]
        for cid in ids:
            options += ["--candidate-id", cid]
        if c["provider"] == "assistant":
            self._run(project, "review", "assistant-prepare", "--stage", "review", "--from-source", "--frames", c["frames"], *options)
            paths = list((job / "assistant" / "review").glob("*/packet.json"))
            folder = max(paths, key=lambda p: p.stat().st_mtime_ns).parent
            project["packets"]["review"] = {"path": str(folder)}
            self._step(project, "review", "waiting")
            project["state"] = "waiting_review"
        else:
            self._run(project, "review", "clips", *options)
            self._run(project, "review", "review-video", *options)
            self._run(project, "review", "report")
            project["state"] = "review_ready"

    def _create_plan(self, project, payload):
        from .render import load_render_plan
        job, c = Path(project["job_path"]), project["config"]
        source = read(job / "job.json")
        candidate = next((r for r in read(job / "candidates.json") if r["id"] == payload.get("candidate_id")), None)
        if not candidate:
            raise ValueError("편집할 후보를 선택하세요.")
        for key in ("logo", "font"):
            c[key] = self._file_value(c.get(key, ""), key, True)
        if not c.get("channel", "").strip():
            raise ValueError("프로젝트 디자인 설정에 채널 이름을 입력하세요.")
        title = payload.get("title_lines") or (c.get("title_override") or "방송 하이라이트").splitlines()
        a, b = candidate["start"], candidate["end"]
        suffix = uuid.uuid4().hex[:8]
        plan = {"source": source["video"], "source_start": 0, "logo": c["logo"], "font": c["font"],
                "font_name": c["font_name"], "channel": c["channel"], "title_lines": title,
                "slug": "clip-" + suffix, "keep": [[a, b]],
                "captions": grouped_captions(self._json(job / "transcript.json", []), a, b),
                "studio": {"project_id": project["id"], "candidate_id": candidate["id"], "draft": True,
                           "note": "전사 기반 편집 초안. 자막과 컷 경계를 직접 확인하세요."}}
        path = self.projects_root / project["id"] / "plans" / ("clip-" + suffix + ".json")
        save(path, plan)
        try:
            load_render_plan(path)
            item = self.library.add_plan(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        project["plans"].append({"path": str(path), "item_id": item["id"], "candidate_id": candidate["id"]})
        self._step(project, "edit", "done")
        project["state"] = "editing"
        self._persist(project)
        return {"item_id": item["id"], "project": self.detail(project["id"])}

    def _import_plan(self, project, payload):
        from .render import load_render_plan
        plan = copy.deepcopy(payload.get("plan"))
        if not isinstance(plan, dict):
            raise ValueError("편집안 JSON이 필요합니다.")
        source = read(Path(project["job_path"]) / "job.json")
        # A project import cannot silently switch to another source video.
        if Path(plan.get("source", "")).resolve() != Path(source["video"]).resolve() or plan.get("source_start", 0) != 0:
            raise ValueError("현재 프로젝트 원본 영상과 source_start=0을 사용하는 편집안을 가져오세요.")
        for key in ("logo", "font"):
            plan[key] = self._file_value(plan.get(key, ""), key, True)
        plan["source"] = source["video"]
        path = self.projects_root / project["id"] / "plans" / ("import-" + uuid.uuid4().hex[:8] + ".json")
        save(path, plan)
        try:
            load_render_plan(path)
            item = self.library.add_plan(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        project["plans"].append({"path": str(path), "item_id": item["id"]})
        self._step(project, "edit", "done")
        project["state"] = "editing"
        self._persist(project)
        return {"item_id": item["id"], "project": self.detail(project["id"])}

    def _render(self, project, payload):
        item_id = payload.get("item_id")
        if item_id not in {p["item_id"] for p in project["plans"]}:
            raise ValueError("이 프로젝트의 편집안을 선택하세요.")
        self._step(project, "render", "running")
        session = self.library.get_session(item_id)
        session.start_render({})
        while True:
            if self.closing:
                raise RuntimeError("서버가 종료되었습니다.")
            with session.lock:
                status = dict(session.job)
            if status["state"] != "running":
                break
            time.sleep(.25)
        if status["state"] == "error":
            raise ValueError(status["error"])
        self._step(project, "render", "done")
        project["state"] = "complete"
        self._log(project, "영상 생성 완료: " + status["path"])

    def action(self, project_id, payload):
        if not isinstance(payload, dict):
            raise ValueError("작업 정보가 필요합니다.")
        name = payload.get("action")
        if name == "save-defaults":
            with self.lock:
                settings = self._validate_settings({k: v for k, v in payload.items() if k != "action"}, defaults=True)
                combined = {**self.defaults, **settings}
                self._validate_settings(combined)
                save(self.root / "settings.json", combined)
                self.defaults = combined
                return {"defaults": copy.deepcopy(self.defaults)}
        with self.lock:
            project = self._get(project_id)
            if self.active == project_id:
                raise ValueError("이 프로젝트의 현재 작업이 끝난 뒤 다시 실행하세요.")
            if name == "settings":
                settings = self._validate_settings(payload.get("config"))
                merged = {**project["config"], **settings}
                if merged["min_length"] > merged["max_length"]:
                    raise ValueError("최소 길이는 최대 길이보다 길 수 없습니다.")
                analysis_changed = any(merged.get(k) != project["config"].get(k)
                                       for k in ("request", "min_length", "max_length", "caption_notes", "reference", "allow_no_chat"))
                project["config"] = merged
                if analysis_changed and (Path(project["job_path"]) / "job.json").is_file():
                    self._write_request(project)
                    for key in ("analysis", "review"):
                        self._step(project, key, "pending", persist=False)
                    project["state"] = "ready_for_analysis"
                    self._log(project, "분석 요청이 변경되었습니다. 분석 자료를 다시 준비하세요. 기존 응답은 재사용할 수 없습니다.")
                self._persist(project)
                return self.detail(project_id)
            if name in {"create-plan", "import-plan"}:
                return (self._create_plan if name == "create-plan" else self._import_plan)(project, payload)
            if name in {"analyze", "prepare-review"} and project["config"]["provider"] == "gemini" and payload.get("allow_external") is not True:
                raise ValueError("Gemini를 실행하려면 외부 API 전송·비용 안내를 확인해야 합니다.")
            if name == "confirm-sync":
                if payload.get("confirmed") is not True:
                    raise ValueError("영상과 채팅을 직접 비교한 뒤 확인을 선택하세요.")
                offset = number(payload.get("offset", 0), "채팅 보정", -86400, 86400)

                def confirm():
                    original = read(Path(project["job_path"]) / "job.json")
                    self._run(project, "sync", "sync", "--offset", offset, "--confirmed")
                    if original.get("chat_offset", 0) != offset or original.get("sync_status") != "confirmed":
                        for key in ("signals", "analysis", "review"):
                            self._step(project, key, "pending", persist=False)
                        self._log(project, "싱크 확인이 변경되었습니다. 반응 신호와 분석 자료를 다시 준비하세요.")
                    project["state"] = "ready_for_analysis"
                return self._start(project, name, confirm)
            actions = {"prepare": lambda: self._prepare(project), "analyze": lambda: self._analyze(project),
                       "import-text": lambda: self._import_response(project, payload, "text"),
                       "prepare-review": lambda: self._review(project, payload),
                       "import-review": lambda: self._import_response(project, payload, "review"),
                       "render": lambda: self._render(project, payload)}
            if name not in actions:
                raise ValueError("지원하지 않는 제작 단계입니다.")
            return self._start(project, name, actions[name])
