from __future__ import annotations

import argparse
import importlib.util
import math
import shutil
import subprocess
import sys
from pathlib import Path

from .core import read, save


def main():
    parser = argparse.ArgumentParser(description="치지직 2시간 하이라이트 분석 데모")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="설치 상태 점검")
    for name in ("ingest", "chats", "transcribe", "signals", "text-candidates", "combine", "clips", "review-video", "report", "sync-report", "sync", "add"):
        p = sub.add_parser(name)
        p.add_argument("--job", default="runs/demo")
        if name == "ingest":
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument("--url")
            group.add_argument("--local-video")
            p.add_argument("--start", type=int, default=0)
            p.add_argument("--duration", type=int, default=7200)
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
        if getattr(args, "limit", 1) <= 0 or getattr(args, "batch_size", 1) <= 0:
            raise ValueError("limit/batch-size는 양수여야 합니다.")
        if args.command == "doctor":
            print(f"Python: {sys.version.split()[0]}")
            for exe in ("ffmpeg", "ffprobe", "nvidia-smi"):
                print(f"{exe}: {shutil.which(exe) or '없음'}")
            for module in ("yt_dlp", "numpy", "faster_whisper", "google.genai"):
                try:
                    available = importlib.util.find_spec(module) is not None
                except ModuleNotFoundError:
                    available = False
                print(f"{module}: {'설치됨' if available else '없음'}")
            if shutil.which("nvidia-smi"):
                subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv"], check=False)
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
            print("보정 저장. signals → combine → report를 다시 실행해 파생 결과를 갱신하세요.")
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
