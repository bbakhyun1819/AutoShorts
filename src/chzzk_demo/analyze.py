from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

from .core import chat_bins, chunks, fingerprint, merge_candidates, read, save
from .media import audio_levels, extract


def transcribe(args):
    # Resolve CUDA runtime DLLs only in this process, never modify system PATH.
    import importlib.util
    for package in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
        try:
            spec = importlib.util.find_spec(package)
            for location in (spec.submodule_search_locations or []) if spec else []:
                folder = Path(location) / "bin"
                if folder.is_dir():
                    os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
        except ModuleNotFoundError:
            pass
    from faster_whisper import BatchedInferencePipeline, WhisperModel
    job = Path(args.job)
    config = read(job / "job.json")
    settings = {"model": args.model, "device": args.device, "compute": args.compute,
                "batch": args.batch_size, "language": "ko", "words": True, "beam": 5}
    signature = fingerprint([config["video"], config["start"], config["end"], settings])
    folder = job / "asr" / signature
    folder.mkdir(parents=True, exist_ok=True)
    model = None
    all_words = []
    timings = []
    for window in chunks(config["start"], config["end"]):
        result = folder / f"{window['index']:03}.json"
        if result.exists():
            data = read(result)
        else:
            audio = folder / f"{window['index']:03}.wav"
            if not audio.exists():
                extract(config["video"], audio, window["start"], window["end"]-window["start"], audio=True)
            if model is None:
                model = BatchedInferencePipeline(WhisperModel(args.model, device=args.device, compute_type=args.compute))
            started = time.perf_counter()
            segments, _ = model.transcribe(str(audio), language="ko", beam_size=5,
                                          batch_size=args.batch_size, word_timestamps=True,
                                          vad_filter=True)
            words = []
            for segment in segments:
                for word in segment.words or []:
                    a, b = word.start + window["start"], word.end + window["start"]
                    if window["core_start"] <= (a+b)/2 < window["core_end"]:
                        words.append({"start": max(config["start"], a), "end": min(config["end"], b),
                                      "text": word.word, "probability": word.probability})
            data = {"window": window, "words": words,
                    "elapsed_seconds": time.perf_counter()-started, "settings": settings}
            save(result, data)
        all_words.extend(data["words"])
        timings.append({"index": window["index"], "audio_seconds": window["end"]-window["start"],
                        "inference_seconds": data["elapsed_seconds"]})
        print(f"대본 {window['index']+1} 조각 완료 ({data['elapsed_seconds']:.1f}초)", flush=True)
    all_words.sort(key=lambda x: x["start"])
    save(job / "transcript.json", all_words)
    save(job / "benchmark.json", {"settings": settings, "chunks": timings,
                                  "note": "추론 시간. 음성 추출·모델 로딩 제외. GPU 최고 메모리는 별도 측정 필요."})


def signals(args):
    job = Path(args.job)
    config = read(job / "job.json")
    start, end = config["start"], config["end"]
    if not (job / "chats.json").exists() and not args.allow_no_chat:
        raise ValueError("채팅 로그가 없습니다. 의도적으로 생략하려면 --allow-no-chat을 지정하세요.")
    if (job / "chat-status.json").exists() and read(job / "chat-status.json")["status"] == "failed" and not args.allow_no_chat:
        raise ValueError("채팅 수집 실패를 먼저 해결하세요. 또는 --allow-no-chat으로 채팅 없이 진행하세요.")
    messages = [] if args.allow_no_chat else read(job / "chats.json")
    messages = [dict(m, time=m["time"]+config.get("chat_offset", 0)) for m in messages]
    bins = chat_bins(messages, start, end)
    save(job / "chat-bins.json", bins)
    audio = job / "analysis.wav"
    if not audio.exists():
        extract(config["video"], audio, start, end-start, audio=True)
    levels = audio_levels(audio, start)
    save(job / "audio-levels.json", levels)
    candidates = []

    def add(t, source, reason, priority):
        candidates.append({"start": max(start, t-45), "end": min(end, t+30),
                           "sources": [source], "reason": reason, "priority": priority,
                           "category": "미분류", "status": "검토 필요"})

    for b in bins:
        if b["count"] >= 5 and b["ratio"] >= 3:
            add(b["time"], "chat", f"채팅 {b['count']}개, 주변 대비 {b['ratio']:.1f}배", min(b["ratio"], 10))
    for i, level in enumerate(levels):
        local = [x["db"] for x in levels[max(0, i-30):i+31]]
        baseline = statistics.median(local)
        if level["db"] > -24 and level["db"]-baseline >= 10:
            add(level["time"], "audio", "상대 음량 급증 (게임 효과음일 수 있음)", 3)
    save(job / "signal-candidates.json", merge_candidates(candidates))
    print(f"신호 후보 {len(merge_candidates(candidates))}개. 무반응 구간은 탈락 처리하지 않습니다.")


def combine(args):
    job = Path(args.job)
    config = read(job / "job.json")
    items = []
    for name in ("signal-candidates.json", "text-candidates.json", "manual-candidates.json"):
        if (job / name).exists():
            candidates = read(job / name)
            if name == "text-candidates.json":
                from .assistant import validate_text_result
                validate_text_result(job, candidates)
            items.extend(candidates)
    merged = merge_candidates(items)
    save(job / "candidates.json", merged)
    print(f"전체 {len(merged)}개 저장. 임의로 상위 후보만 남기지 않습니다.")


def clips(args):
    job = Path(args.job)
    config = read(job / "job.json")
    candidates = read(job / "candidates.json")
    for item in candidates[:args.limit]:
        output = job / "clips" / f"{item['id']}.mp4"
        if not output.exists():
            extract(config["video"], output, item["start"], item["end"]-item["start"])
        print(f"클립 생성: {item['id']}", flush=True)
    print(f"클립 생성 상한 {args.limit}개. 나머지 후보는 목록에 보존됩니다.")
