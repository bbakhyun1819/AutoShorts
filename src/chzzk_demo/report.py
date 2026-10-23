from __future__ import annotations

import html
import json
from pathlib import Path

from .core import fingerprint, read, stamp
from .media import extract


def applicable_reviews(job, items):
    """Choose the newest review that still applies to a current candidate."""
    candidates = {item["id"]: item for item in items}
    reviews = {}
    current_fingerprint = None
    for path in sorted((job / "reviews").glob("*.json"),
                       key=lambda p: (p.stat().st_mtime_ns, p.name)):
        value = read(path)
        candidate = candidates.get(value.get("candidate_id"))
        if candidate is None:
            continue
        if value.get("provider") == "assistant":
            if value.get("candidate_signature") != fingerprint(candidate):
                continue
            if value.get("evidence") not in {"transcript", "sampled_frames"}:
                continue
            if current_fingerprint is None:
                from .assistant import input_fingerprint
                current_fingerprint = input_fingerprint(job, "review")
            if value.get("input_fingerprint") != current_fingerprint:
                continue
        reviews[value["candidate_id"]] = value
    return reviews


def review_label(value):
    if value.get("provider") == "assistant":
        if value["evidence"] == "sampled_frames":
            return "Codex 표본 프레임 검토 (전체 영상·음성 미검토)"
        return "Codex 대본 검토 (영상·음성 미검토)"
    model = value.get("model")
    return f"Gemini 영상 검토 ({model})" if model else "Gemini 영상 검토"


def semantic_status(job):
    path = job / "text-candidates.json"
    if not path.exists():
        return "대본 의미 분석 미실행"
    items = read(path)
    metadata_path = job / "text-analysis.json"
    metadata = read(metadata_path) if metadata_path.exists() else {}
    assistant_items = any("assistant" in item.get("sources", []) for item in items)
    if not assistant_items and not (not items and metadata.get("provider") == "assistant"):
        return "대본 의미 분석 완료 (Gemini)"
    if (metadata.get("provider") != "assistant"
            or metadata.get("candidates_fingerprint") != fingerprint(items)):
        return "Codex 대본 분석 상태 확인 필요 (결과 정보 불일치)"
    from .assistant import input_fingerprint
    if metadata.get("input_fingerprint") != input_fingerprint(job, "text"):
        return "Codex 대본 분석 결과 오래됨 (입력 변경, 다시 분석 필요)"
    reviewed = metadata.get("reviewed_windows", [])
    total = metadata.get("total_windows")
    completed = len(set(reviewed))
    if metadata.get("complete") is True and type(total) is int and completed == total:
        return f"Codex 대본 의미 분석 완료 ({completed}/{total}구간)"
    return f"Codex 대본 의미 분석 일부 완료 ({completed}/{total}구간)"


def report(args):
    job = Path(args.job)
    config = read(job / "job.json")
    items = read(job / "candidates.json") if (job / "candidates.json").exists() else []
    messages = read(job / "chats.json") if (job / "chats.json").exists() else []
    words = read(job / "transcript.json") if (job / "transcript.json").exists() else []
    reviews = applicable_reviews(job, items)
    rows = []
    for item in items:
        item = dict(item)
        item["clip"] = f"clips/{item['id']}.mp4" if (job / "clips" / f"{item['id']}.mp4").exists() else None
        item["chat"] = [{"time": m["time"]+config.get("chat_offset", 0)-item["start"], "text": m["text"]}
                        for m in messages if item["start"] <= m["time"]+config.get("chat_offset", 0) < item["end"]]
        item["transcript"] = " ".join(w["text"] for w in words if item["start"] <= w["start"] < item["end"])
        item["review"] = reviews.get(item["id"])
        item["review_label"] = review_label(item["review"]) if item["review"] else None
        rows.append(item)
    payload = json.dumps(rows, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    page = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AutoShorts 후보 검수</title>
<style>body{font-family:system-ui;background:#101827;color:#e2e8f0;max-width:1150px;margin:32px auto;padding:20px}
article{background:#1e293b;padding:24px;margin:24px 0;border-radius:12px}video{width:100%;max-height:420px}
.grid{display:grid;grid-template-columns:2fr 1fr;gap:20px}.chat{height:300px;overflow:auto;background:#0f172a;padding:12px}
button,select{padding:10px;margin:8px;background:#c7d2fe;border:0;border-radius:6px}p{line-height:1.6}small{color:#94a3b8}
@media(max-width:700px){.grid{grid-template-columns:1fr}}</style>
<h1>AutoShorts · 후보 검수</h1><p>자동 선정은 초안입니다. 채팅 싱크·내용·컷 지점을 직접 확인하세요.</p>
<p>__STATUS__</p><p>무반응 구간은 제외 판정이 아닙니다. 조용한 게임 명장면·춤 자동 탐지는 아직 제한적입니다.</p>
<button id="download">선택 결과 JSON 저장</button><div id="list"></div>
<script>const data=__DATA__; const root=document.getElementById('list');
const el=(tag,text)=>{let e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e};
for(const c of data){let a=el('article');a.append(el('h2',`${c.category} · ${c.start.toFixed(1)}–${c.end.toFixed(1)}초`));
a.append(el('p',c.reason));a.append(el('small',`근거: ${c.sources.join(', ')} / ID: ${c.id}`));
let grid=el('div');grid.className='grid';let left=el('div');let chat=el('div');chat.className='chat';
if(c.clip){let v=el('video');v.controls=true;v.preload='metadata';v.src=c.clip;left.append(v);
v.ontimeupdate=()=>{chat.replaceChildren(...c.chat.filter(m=>m.time<=v.currentTime&&m.time>v.currentTime-15).slice(-30).map(m=>el('p',m.text)));};}
else left.append(el('p','아직 클립을 생성하지 않은 후보입니다. clips --limit 값을 늘려 실행하세요.'));
grid.append(left,chat);a.append(grid);let d=el('details');d.append(el('summary','대본 보기'),el('p',c.transcript||'대본 없음'));a.append(d);
if(c.review)a.append(el('p',`${c.review_label}: ${c.review.status} / ${c.review.reason} / 제안 원본 구간 ${c.review.source_start.toFixed(1)}–${c.review.source_end.toFixed(1)}초`));
let s=el('select');for(const t of ['미검토','편집 선택','보류','제외'])s.append(el('option',t));
c.human_decision='미검토';s.onchange=()=>c.human_decision=s.value;a.append(s);root.append(a);}
if(!data.length)root.append(el('p','후보가 없습니다. 분석 실행 여부를 확인하거나 수동 후보를 추가하세요.'));
document.getElementById('download').onclick=()=>{let out=data.map(({chat,transcript,clip,review_label,...x})=>x);
let u=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:'application/json'}));let a=el('a');a.href=u;a.download='decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);};
</script></html>"""
    semantic = semantic_status(job)
    transcript_count = sum(v.get("provider") == "assistant" and v.get("evidence") == "transcript"
                           for v in reviews.values())
    frames_count = sum(v.get("provider") == "assistant" and v.get("evidence") == "sampled_frames"
                      for v in reviews.values())
    video_count = len(reviews) - transcript_count - frames_count
    status = (f"분석 범위 {stamp(config['start'])}~{stamp(config['end'])} · 싱크: {config.get('sync_status', 'unverified')}"
              f" · 채팅 보정 {config.get('chat_offset', 0)}초 · {semantic}"
              f" · Codex 대본 검토 {transcript_count}개 · Codex 표본 프레임 검토 {frames_count}개"
              f" · Gemini 영상 검토 {video_count}개 (현재 후보에 연결된 최신 검토)")
    page = page.replace("__STATUS__", html.escape(status)).replace("__DATA__", payload)
    (job / "report.html").write_text(page, encoding="utf-8")
    print(f"검수 페이지: {(job / 'report.html').resolve()}")


def sync_report(args):
    job = Path(args.job)
    config = read(job / "job.json")
    # Use the same report renderer in an isolated sub-job, with proxy clips at 3 points.
    folder = job / "sync-check"
    folder.mkdir(exist_ok=True)
    duration = config["end"]-config["start"]
    items = []
    from .core import save
    for i, fraction in enumerate((.05, .5, .95)):
        start = config["start"] + max(0, duration*fraction-15)
        end = min(config["end"], start+30)
        output = folder / "clips" / f"sync-{i}.mp4"
        if not output.exists():
            extract(config["video"], output, start, end-start)
        items.append({"id": f"sync-{i}", "start": start, "end": end, "category": "싱크 점검",
                      "sources": ["sync"], "reason": "웹 다시보기의 같은 시점과 채팅을 비교하세요.", "priority": 0})
    save(folder / "job.json", config)
    save(folder / "candidates.json", items)
    save(folder / "chats.json", read(job / "chats.json") if (job / "chats.json").exists() else [])
    from types import SimpleNamespace
    report(SimpleNamespace(job=str(folder)))
