from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def stamp(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def chunks(start, end, size=1200, overlap=5):
    """Core windows own midpoint timestamps; context overlap does not own duplicates."""
    index = 0
    while start < end:
        core_end = min(end, start + size)
        yield {"index": index, "core_start": start, "core_end": core_end,
               "start": max(0, start - overlap), "end": min(end, core_end + overlap)}
        start = core_end
        index += 1


def chat_key(raw):
    return fingerprint([raw.get("messageTime"), raw.get("playerMessageTime"),
                        raw.get("userIdHash"), raw.get("content"), raw.get("messageTypeCode")])


def normalize_chat(raw, offset=0):
    t = raw.get("playerMessageTime")
    if not isinstance(t, (float, int)) or not math.isfinite(t):
        return None
    return {"time": t / 1000 + offset, "text": str(raw.get("content") or ""),
            "user": str(raw.get("userIdHash") or ""), "id": chat_key(raw)}


def chat_bins(messages, start, end, width=10):
    bins = [{"time": t, "count": 0, "users": set(), "laugh": 0, "examples": []}
            for t in range(int(start), math.ceil(end), width)]
    seen = set()
    for m in messages:
        if not start <= m["time"] < end:
            continue
        index = int((m["time"] - start) // width)
        if index >= len(bins):
            continue
        # Limit identical messages from the same sender in each bin.
        key = (index, m["user"], m["text"])
        if key in seen:
            continue
        seen.add(key)
        b = bins[index]
        b["count"] += 1
        if m["user"]:
            b["users"].add(m["user"])
        b["laugh"] += int("ㅋㅋ" in m["text"] or "ㅎㅎ" in m["text"])
        if len(b["examples"]) < 5:
            b["examples"].append(m["text"][:120])
    for index, b in enumerate(bins):
        neighbors = [x["count"] for j, x in enumerate(bins[max(0, index-18):index+19], max(0, index-18)) if j != index]
        baseline = statistics.median(neighbors) if neighbors else 0
        b["ratio"] = b["count"] / max(1, baseline)
        b["users"] = len(b["users"])
    return bins


def merge_candidates(items, max_span=180):
    """Merge strongly overlapping windows, never merge an unlimited chain."""
    out = []
    for item in sorted(items, key=lambda x: x["start"]):
        item = dict(item)
        if out:
            previous = out[-1]
            overlap = min(previous["end"], item["end"]) - max(previous["start"], item["start"])
            shortest = min(previous["end"]-previous["start"], item["end"]-item["start"])
            if overlap > 0 and overlap / shortest >= .5 and item["end"]-previous["start"] <= max_span:
                previous["end"] = max(previous["end"], item["end"])
                previous["sources"] = sorted(set(previous["sources"] + item["sources"]))
                previous["reason"] += " / " + item["reason"]
                previous["priority"] = max(previous["priority"], item["priority"])
                if previous["category"] != item["category"]:
                    previous["category"] = "미분류"
                continue
        out.append(item)
    for item in out:
        item["id"] = fingerprint([item["start"], item["end"], item["sources"]])[:10]
    return sorted(out, key=lambda x: (-x["priority"], x["start"]))


def valid_ai_candidate(item, start, end):
    try:
        a, b = float(item["start"]), float(item["end"])
    except (KeyError, ValueError, TypeError):
        return False
    return (math.isfinite(a) and math.isfinite(b) and start <= a < b <= end
            and b-a <= 180 and item.get("category") in {"토크", "게임", "노래·춤", "미분류"})
