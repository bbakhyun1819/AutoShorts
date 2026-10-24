from __future__ import annotations

import argparse
import importlib.util
import math
import shutil
import subprocess
import sys
from pathlib import Path

from .core import read, save
from .workflow_lock import studio_job_lock


def main():
    parser = argparse.ArgumentParser(description="치지직 하이라이트 분석 데모 (기본 2시간, --duration으로 범위 지정)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="설치 상태 점검")
    render_parser = sub.add_parser("render", help="JSON 편집 계획으로 세로 쇼츠·자막 생성")
    render_parser.add_argument("--plan", required=True, help="원본 경로·사용 구간·자막을 담은 JSON")
    render_parser.add_argument("--output", required=True, help="영상·자막·manifest 저장 폴더")
    render_parser.add_argument("--gallery", action="store_true", help="같은 출력 폴더의 비교 페이지도 갱신")
    editor_parser = sub.add_parser("editor", help="쇼츠 라이브러리와 디자인 편집기 실행")
    editor_input = editor_parser.add_mutually_exclusive_group(required=True)
    editor_input.add_argument("--plan", help="편집할 로컬 JSON 편집안 한 개")
    editor_input.add_argument("--library", help="렌더 결과 폴더 또는 라이브러리 JSON")
    editor_parser.add_argument("--output", help="영상별 편집안·생성 영상 저장 폴더")
    editor_parser.add_argument("--port", type=int, default=0, help="로컬 포트 (기본: 자동 선택)")
    editor_parser.add_argument("--no-open", action="store_true", help="브라우저 자동 열기 생략")
    studio_parser = sub.add_parser("studio", help="프로젝트 수집·분석·자막·컷 편집 작업실 실행")
    studio_parser.add_argument("--workspace", default=".", help="프로젝트와 runs/studio를 보관할 작업 폴더")
    studio_parser.add_argument("--library", help="함께 표시할 기존 쇼츠 목록 JSON 또는 렌더 폴더")
    studio_parser.add_argument("--output", help="영상별 편집안·출력 저장 폴더")
    studio_parser.add_argument("--port", type=int, default=0)
    studio_parser.add_argument("--no-open", action="store_true")
    for name in ("ingest", "chats", "transcribe", "signals", "text-candidates", "combine", "clips", "review-video", "report", "sync-report", "sync", "add", "assistant-prepare", "assistant-import"):
        p = sub.add_parser(name)
        p.add_argument("--job", default="runs/demo")
        if name == "ingest":
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument("--url")
            group.add_argument("--local-video")
            p.add_argument("--start", type=int, default=0)
            p.add_argument("--duration", type=int, default=7200)
            p.add_argument("--format", help="다운로드 화질 선택식 (yt-dlp 형식, 예: best[height<=480])")
            p.add_argument("--cookies", help="필요한 경우에만 Netscape 쿠키 파일 경로")
        elif name == "chats":
            p.add_argument("--cookies")
        elif name == "transcribe":
            p.add_argument("--model", default="large-v3")
            p.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
            p.add_argument("--compute", choices=["float16", "int8_float16", "int8", "float32"], default="float16")
            p.add_argument("--batch-size", type=int, default=8)
        elif name == "signals":
            p.add_argument("--allow-no-chat", action="store_true")
        elif name in ("clips", "review-video"):
            p.add_argument("--limit", type=int, default=10)
            p.add_argument("--candidate-id", action="append", help="이 후보만 처리 (여러 번 지정 가능)")
        elif name == "assistant-prepare":
            p.add_argument("--stage", choices=["text", "review"], default="text")
            p.add_argument("--limit", type=int, default=10, help="검토 자료에 넣을 상위 후보 수")
            p.add_argument("--frames", type=int, default=6, help="후보별 샘플 화면 수(0~24), 0은 대본만 검토")
            p.add_argument("--candidate-id", action="append", help="이 후보만 검토 자료에 포함 (여러 번 지정 가능)")
            p.add_argument("--from-source", action="store_true", help="review 화면을 후보 클립 대신 원본 영상에서 추출")
        elif name == "assistant-import":
            p.add_argument("--input", required=True, help="assistant가 작성한 응답 JSON")
        elif name == "sync":
            p.add_argument("--offset", type=float, required=True, help="채팅을 늦추면 양수, 앞당기면 음수(초)")
            p.add_argument("--confirmed", action="store_true", help="직접 비교 확인했을 때만 지정")
        elif name == "add":
            p.add_argument("--start", type=float, required=True)
            p.add_argument("--end", type=float, required=True)
            p.add_argument("--category", choices=["토크", "게임", "노래·춤", "미분류"], required=True)
            p.add_argument("--reason", default="사용자 지정 구간")
    args = parser.parse_args()
    try:
        with studio_job_lock(getattr(args, "job", None)):
            if getattr(args, "limit", 1) <= 0 or getattr(args, "batch_size", 1) <= 0:
                raise ValueError("limit/batch-size는 양수여야 합니다.")
            if args.command == "doctor":
                print(f"Python: {sys.version.split()[0]}")
                for exe in ("ffmpeg", "ffprobe", "nvidia-smi"):
                    print(f"{exe}: {shutil.which(exe) or '없음'}")
                for module in ("yt_dlp", "numpy", "faster_whisper", "google.genai", "PIL"):
                    try:
                        available = importlib.util.find_spec(module) is not None
                    except ModuleNotFoundError:
                        available = False
                    print(f"{module}: {'설치됨' if available else '없음'}")
                if shutil.which("nvidia-smi"):
                    subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv"], check=False)
                return
            if args.command == "render":
                from .render import render_plan
                render_plan(args.plan, args.output, gallery=args.gallery)
                print(f"세로 영상 생성 완료: {Path(args.output).resolve()}")
                return
            if args.command == "editor":
                if not 0 <= args.port <= 65535:
                    raise ValueError("port는 0~65535여야 합니다.")
                from .editor import serve_editor
                serve_editor(args.plan, args.output, library_path=args.library,
                             port=args.port, open_browser=not args.no_open)
                return
            if args.command == "studio":
                if not 0 <= args.port <= 65535:
                    raise ValueError("port는 0~65535여야 합니다.")
                from .editor import serve_editor
                workspace = Path(args.workspace).resolve()
                workspace.mkdir(parents=True, exist_ok=True)
                library = Path(args.library).resolve() if args.library else workspace / "runs" / "studio" / "library"
                if not args.library:
                    library.mkdir(parents=True, exist_ok=True)
                output = args.output or str(workspace / "runs" / "studio" / "editor")
                serve_editor(output_dir=output, library_path=library, workspace=workspace,
                             port=args.port, open_browser=not args.no_open)
                return
            if args.command in ("ingest", "transcribe", "signals", "clips", "sync-report"):
                from .media import require_tools
                require_tools()
            if args.command in ("ingest", "chats"):
                from . import ingest
                getattr(ingest, args.command)(args)
            elif args.command in ("transcribe", "signals", "combine", "clips"):
                from . import analyze
                getattr(analyze, args.command)(args)
            elif args.command in ("text-candidates", "review-video"):
                from . import ai
                getattr(ai, args.command.replace("-", "_"))(args)
            elif args.command in ("assistant-prepare", "assistant-import"):
                from . import assistant
                (assistant.prepare if args.command == "assistant-prepare" else assistant.import_result)(args)
            elif args.command in ("report", "sync-report"):
                from . import report
                getattr(report, args.command.replace("-", "_"))(args)
            elif args.command == "sync":
                if not math.isfinite(args.offset):
                    raise ValueError("보정 시간은 유한한 수여야 합니다.")
                path = Path(args.job) / "job.json"
                value = read(path)
                value.update(chat_offset=args.offset, sync_status="confirmed" if args.confirmed else "unverified")
                save(path, value)
                print("보정 저장. signals를 다시 실행하세요. assistant 대본 결과가 있으면 assistant-prepare "
                      "--stage text → 재분석 → assistant-import 후 combine을 실행하세요. "
                      "이어서 clips/검토 자료·결과와 report도 갱신하세요.")
            elif args.command == "add":
                job = Path(args.job)
                config = read(job / "job.json")
                if not config["start"] <= args.start < args.end <= config["end"] or args.end-args.start > 180:
                    raise ValueError("분석 범위 안의 최대 180초 구간을 지정하세요.")
                path = job / "manual-candidates.json"
                items = read(path) if path.exists() else []
                items.append({"start": args.start, "end": args.end, "category": args.category,
                              "reason": args.reason, "sources": ["manual"], "priority": 10, "status": "검토 필요"})
                save(path, items)
                print("수동 후보 저장. combine → clips → report 순서로 갱신하세요.")
    except Exception as error:
        print(f"실패: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
