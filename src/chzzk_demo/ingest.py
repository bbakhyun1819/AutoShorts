from __future__ import annotations

import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .core import normalize_chat, read, save
from .media import probe, run


def ingest(args):
    job = Path(args.job).resolve()
    job.mkdir(parents=True, exist_ok=True)
    if (job / "job.json").exists():
        raise ValueError("이 작업 폴더는 이미 등록되어 있습니다. 새 폴더를 지정하거나 후속 단계를 실행하세요.")
    if not 0 < args.duration <= 7200 or args.start < 0:
        raise ValueError("데모 분석 길이는 0초 초과 7200초 이하, 시작은 0초 이상입니다.")
    if args.local_video:
        video = Path(args.local_video).resolve()
        if not video.is_file():
            raise ValueError("로컬 영상이 없습니다.")
        video_id = "local"
    else:
        match = re.fullmatch(r"https://chzzk\.naver\.com/video/(\d+)/?(?:\?.*)?", args.url or "")
        if not match:
            raise ValueError("https://chzzk.naver.com/video/영상번호 형식의 URL이 필요합니다.")
        video_id = match.group(1)
        # Preserve the complete original; only analysis is capped to two hours.
        listing = job / "downloaded-path.txt"
        command = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--write-info-json",
                   "--merge-output-format", "mp4", "-o", str(job / "source.%(ext)s"),
                   "--print-to-file", "after_move:filepath", str(listing)]
        if args.cookies:
            command += ["--cookies", args.cookies]
        run(command + [args.url])
        video = Path(listing.read_text(encoding="utf-8").strip().splitlines()[-1]).resolve()
    meta = probe(video)
    duration = float(meta["format"]["duration"])
    if not any(x["codec_type"] == "audio" for x in meta["streams"]):
        raise ValueError("영상에 오디오 트랙이 없습니다.")
    end = min(duration, args.start + args.duration)
    if args.start >= end:
        raise ValueError("분석 시작 시간이 영상 길이를 넘습니다.")
    save(job / "media-info.json", meta)
    save(job / "job.json", {"video_id": video_id, "url": args.url, "video": str(video),
                            "start": args.start, "end": end, "source_duration": duration,
                            "sync_status": "unverified", "chat_offset": 0})
    print(f"원본 보존 완료. 분석 구간: {args.start} ~ {end}초. 채팅은 chats 명령으로 수집합니다.")


def chats(args):
    job = Path(args.job)
    config = read(job / "job.json")
    if config["video_id"] == "local":
        raise ValueError("로컬 영상은 chats.json을 직접 넣거나 --allow-no-chat으로 분석하세요.")
    jar = http.cookiejar.MozillaCookieJar()
    if args.cookies:
        jar.load(args.cookies, ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    cache = job / "chat-pages"
    cache.mkdir(exist_ok=True)
    cursor = max(0, int(config["start"] * 1000) - 60000)
    end_ms = int(config["end"] * 1000)
    normalized = {}
    invalid = 0
    requests = 0
    # Re-read saved pages on resume; no loss of data between page and cursor writes.
    try:
        while cursor <= end_ms:
            page = cache / f"{cursor}.json"
            if page.exists():
                body = read(page)
            else:
                url = (f"https://api.chzzk.naver.com/service/v1/videos/{config['video_id']}/chats"
                       f"?playerMessageTime={cursor}&previousVideoChatSize=50")
                for attempt in range(4):
                    try:
                        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                        with opener.open(request, timeout=30) as response:
                            body = json.load(response)
                        if body.get("code") != 200 or not isinstance(body.get("content"), dict):
                            raise ValueError("채팅 API 응답 형식/권한을 확인하세요.")
                        break
                    except urllib.error.HTTPError as error:
                        if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                            raise
                        time.sleep(2 ** attempt)
                    except (TimeoutError, urllib.error.URLError):
                        if attempt == 3:
                            raise
                        time.sleep(2 ** attempt)
                save(page, body)
                time.sleep(.25)
            content = body["content"]
            for raw in (content.get("previousVideoChats") or []) + (content.get("videoChats") or []):
                message = normalize_chat(raw)
                if message is None:
                    invalid += 1
                elif config["start"] - 60 <= message["time"] < config["end"] + 60:
                    normalized[message["id"]] = message
            requests += 1
            nxt = content.get("nextPlayerMessageTime")
            if nxt is None:
                break
            nxt = int(nxt)
            if nxt <= cursor:
                raise ValueError("채팅 페이지 커서가 전진하지 않습니다. 누락 가능성이 있어 중단합니다.")
            cursor = nxt
            if requests > 20000:
                raise ValueError("채팅 페이지 수 상한 초과")
        if invalid:
            raise ValueError(f"재생 시간이 없는 메시지 {invalid}개. 조용히 누락시키지 않고 중단합니다.")
        save(job / "chats.json", sorted(normalized.values(), key=lambda x: x["time"]))
        save(job / "chat-status.json", {"status": "fetched_unverified", "pages": requests,
                                       "count": len(normalized), "invalid": invalid,
                                       "note": "API 순회 종료. 플랫폼 보관 누락 및 영상 싱크는 별도 확인 필요."})
    except Exception as error:
        save(job / "chat-status.json", {"status": "failed", "cursor": cursor,
                                       "pages": requests, "error": str(error)})
        raise
    print(f"채팅 {len(normalized)}개 저장. sync-report로 영상과 비교하세요.")
