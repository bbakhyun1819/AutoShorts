"""Local, plan-driven Shorts rendering. No analysis, download, or API calls."""
from __future__ import annotations

import html
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .core import read, save
from .media import probe, require_tools

STYLES = {"Caption": 58, "Emphasis": 60, "Punchline": 76}
SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
DESIGN_DEFAULTS = {
    "title": {"x": 540, "y": 439, "size": 76, "line_gap": 88},
    "caption": {"x": 540, "y": 1435, "size": 58, "emphasis_size": 60, "punchline_size": 76},
    "channel": {"x": None, "y": 1650, "size": 92},
    "logo": {"x": 365, "y": 1572, "size": 156},
    "context": {"x": 540, "y": 293, "size": 34},
}


def normalize_design(value=None):
    """Merge optional layout overrides with the approved 1080 x 1920 design."""
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - set(DESIGN_DEFAULTS):
        raise ValueError("design: title, caption, channel, logo, context 설정만 사용할 수 있습니다.")
    design = {}
    for layer, defaults in DESIGN_DEFAULTS.items():
        override = value.get(layer, {})
        if not isinstance(override, dict) or set(override) - set(defaults):
            raise ValueError(f"design.{layer}: 알 수 없는 디자인 설정입니다.")
        settings = {**defaults, **override}
        for key, item in settings.items():
            if layer == "channel" and key == "x" and item is None:
                continue
            number = _number(item, f"design.{layer}.{key}")
            if key in {"x", "y"}:
                maximum = 1080 if key == "x" else 1920
                if not 0 <= number <= maximum:
                    raise ValueError(f"design.{layer}.{key}: 0~{maximum} 범위여야 합니다.")
                settings[key] = round(number, 2)
            else:
                low, high = (16, 1080) if layer == "logo" else (0, 480) if key == "line_gap" else (12, 240)
                if not low <= number <= high or not number.is_integer():
                    raise ValueError(f"design.{layer}.{key}: {low}~{high} 범위의 정수여야 합니다.")
                settings[key] = int(number)
        design[layer] = settings
    logo = design["logo"]
    if logo["x"] + logo["size"] > 1080 or logo["y"] + logo["size"] > 1920:
        raise ValueError("design.logo: 로고 전체가 캔버스 안에 들어가야 합니다.")
    return design


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: 유한한 숫자가 필요합니다.")
    return float(value)


def _text(value, label, lines=2):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: 빈 문자열은 사용할 수 없습니다.")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if len(value.split("\n")) > lines or any(ord(c) < 32 and c != "\n" for c in value):
        raise ValueError(f"{label}: 최대 {lines}줄의 일반 텍스트가 필요합니다.")
    return value


def _local_file(value, base, label):
    if not isinstance(value, str) or not value or "://" in value:
        raise ValueError(f"{label}: 로컬 파일 경로가 필요합니다.")
    path = Path(value)
    path = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file():
        raise ValueError(f"{label}: 파일을 찾을 수 없습니다: {path}")
    return path


def validate_plan(plan, *, source_duration, source_width, source_height):
    """Validate absolute source times; return a normalized plan without reading files."""
    if not isinstance(plan, dict):
        raise ValueError("편집 계획은 JSON 객체여야 합니다.")
    slug = plan.get("slug", "short")
    reserved = {"manifest", "index", "con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if not isinstance(slug, str) or not SLUG.fullmatch(slug) or slug in reserved:
        raise ValueError("slug: 영문 소문자·숫자·하이픈·밑줄로 1~64자를 사용하세요.")
    title = plan.get("title_lines")
    if not isinstance(title, list) or not 1 <= len(title) <= 2:
        raise ValueError("title_lines: 제목 1~2줄을 배열로 입력하세요.")
    title = [_text(t, "title_lines", 1) for t in title]
    channel = _text(plan.get("channel"), "channel", 1)
    font_name = _text(plan.get("font_name"), "font_name", 1)
    if any(c in font_name for c in ",{}\\"):
        raise ValueError("font_name: ASS 스타일 구분 문자는 사용할 수 없습니다.")
    offset = _number(plan.get("source_start", 0), "source_start")
    duration = _number(source_duration, "source duration")
    if offset < 0 or duration <= 0:
        raise ValueError("source_start는 0 이상, 원본 길이는 양수여야 합니다.")
    keep = plan.get("keep")
    if not isinstance(keep, list) or not keep:
        raise ValueError("keep: 하나 이상의 [시작초, 종료초] 구간이 필요합니다.")
    ranges, cursor = [], offset
    for i, pair in enumerate(keep):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError(f"keep[{i}]: [시작초, 종료초] 형식이 필요합니다.")
        a, b = (_number(t, f"keep[{i}]") for t in pair)
        if a < cursor or b <= a or b > offset + duration + 0.001:
            raise ValueError("keep: 원본 범위 안에서 시간순으로 겹치지 않는 양수 구간이어야 합니다.")
        if b - a < 1 / 30:
            raise ValueError("keep: 각 구간은 출력 한 프레임(1/30초) 이상이어야 합니다.")
        ranges.append([a, b])
        cursor = b
    captions = plan.get("captions", [])
    if not isinstance(captions, list):
        raise ValueError("captions: 배열이 필요합니다.")
    normalized, previous_end = [], -1
    for i, cue in enumerate(captions):
        if not isinstance(cue, dict):
            raise ValueError(f"captions[{i}]: 객체가 필요합니다.")
        a = _number(cue.get("start"), f"captions[{i}].start")
        b = _number(cue.get("end"), f"captions[{i}].end")
        if b <= a or a < previous_end or not any(x <= a < b <= y for x, y in ranges):
            raise ValueError("captions: 각 자막은 한 keep 구간 안에 있으며 시간순으로 겹치지 않아야 합니다.")
        style = cue.get("style", "Caption")
        if not isinstance(style, str) or style not in STYLES:
            raise ValueError(f"captions[{i}].style: {', '.join(STYLES)} 중 하나여야 합니다.")
        normalized.append(dict(start=a, end=b, text=_text(cue.get("text"), "caption text"), style=style))
        previous_end = b
    total = sum(b - a for a, b in ranges)
    context = plan.get("context", [])
    if not isinstance(context, list):
        raise ValueError("context: 배열이 필요합니다.")
    notes = []
    for cue in context:
        if not isinstance(cue, dict):
            raise ValueError("context: 객체가 필요합니다.")
        a, b = _number(cue.get("start"), "context.start"), _number(cue.get("end"), "context.end")
        if not 0 <= a < b <= total:
            raise ValueError("context: 완성 영상의 0~길이 범위 안이어야 합니다.")
        notes.append(dict(start=a, end=b, text=_text(cue.get("text"), "context.text", 1)))
    crop = plan.get("crop")
    if crop is None:
        unit = int(min(source_width / 4, source_height / 3) // 2) * 2
        width, height = unit * 4, unit * 3
        crop = dict(width=width, height=height, x=int((source_width - width) // 4) * 2,
                    y=int((source_height - height) // 4) * 2)
    if not isinstance(crop, dict) or set(crop) != {"x", "y", "width", "height"}:
        raise ValueError("crop: x, y, width, height가 필요합니다.")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 or v % 2 for v in crop.values()):
        raise ValueError("crop: 좌표와 크기는 0 이상의 짝수 정수여야 합니다.")
    w, h = crop["width"], crop["height"]
    if not w or not h or w * 3 != h * 4 or crop["x"] + w > source_width or crop["y"] + h > source_height:
        raise ValueError("crop: 원본 화면 안에 들어가는 4:3 사각형이 필요합니다.")
    return dict(slug=slug, title_lines=title, channel=channel, font_name=font_name,
                source_start=offset, keep=ranges, captions=normalized, context=notes,
                crop=dict(crop), duration_seconds=total, design=normalize_design(plan.get("design")))


def transform_captions(plan):
    """Map source timestamps onto the concatenated output timeline."""
    result, offset = [], 0.0
    for a, b in plan["keep"]:
        for cue in plan["captions"]:
            if a <= cue["start"] < cue["end"] <= b:
                result.append({**cue, "source_start": cue["start"], "source_end": cue["end"],
                               "start": cue["start"] - a + offset, "end": cue["end"] - a + offset})
        offset += b - a
    return result


def _timestamp(seconds, ass=False):
    scale = 100 if ass else 1000
    h, rest = divmod(round(seconds * scale), 3600 * scale)
    m, rest = divmod(rest, 60 * scale)
    s, fraction = divmod(rest, scale)
    return f"{h}:{m:02}:{s:02}.{fraction:02}" if ass else f"{h:02}:{m:02}:{s:02},{fraction:03}"


def ass_text(text):
    # Literal user text must never become an ASS override or newline command.
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", r"\N")


def _text_layers(plan, captions, font_path):
    """Shared text geometry for ASS output and the interactive preview handles."""
    try:
        from PIL import ImageFont
    except ImportError as exc:
        raise RuntimeError('렌더링에는 Pillow가 필요합니다: pip install -e ".[render]"') from exc

    def fit(text, size, max_width=920):
        text = ass_text(text).replace(r"\N", "\n")
        minimum = min(32, size)
        while size >= minimum:
            font = ImageFont.truetype(str(font_path), size)
            if max(font.getlength(line) for line in text.split("\n")) <= max_width:
                return size
            size -= 1
        raise ValueError("문장이 화면 폭을 넘습니다. 제목·자막을 줄이거나 자막을 두 줄로 나누세요.")

    design = normalize_design(plan.get("design"))
    layers = []

    def layer(identifier, kind, a, b, style, text, size, x, y, width=920):
        fitted = fit(text, size, width)
        font = ImageFont.truetype(str(font_path), fitted)
        escaped = ass_text(text).replace(r"\N", "\n")
        lines = escaped.split("\n")
        ascent, descent = font.getmetrics()
        color = "#00ffaa" if style in {"Emphasis", "Punchline"} else "#b8b8b8" if style == "Context" else "#ffffff"
        layers.append(dict(id=identifier, kind=kind, start=a, end=b, style=style, text=text,
                           x=x, y=y, size=fitted, requested_size=size, color=color,
                           width=round(max(font.getlength(line) for line in lines), 2),
                           height=(ascent + descent) * len(lines)))

    duration = plan["duration_seconds"]
    title = design["title"]
    positions = [title["y"] - title["line_gap"] / 2, title["y"] + title["line_gap"] / 2] if len(plan["title_lines"]) == 2 else [title["y"]]
    for i, (y, text) in enumerate(zip(positions, plan["title_lines"])):
        layer(f"title-{i}", "title", 0, duration, "Title", text, title["size"], title["x"], y)
    channel = design["channel"]
    name_size = fit(plan["channel"], channel["size"], 490)
    name_width = ImageFont.truetype(str(font_path), name_size).getlength(plan["channel"])
    name_x = round(521 + name_width / 2) if channel["x"] is None else channel["x"]
    layer("channel", "channel", 0, duration, "Channel", plan["channel"], channel["size"], name_x, channel["y"], 490)
    caption = design["caption"]
    sizes = {"Caption": caption["size"], "Emphasis": caption["emphasis_size"], "Punchline": caption["punchline_size"]}
    for i, cue in enumerate(captions):
        layer(f"caption-{i}", "caption", cue["start"], cue["end"], cue["style"], cue["text"], sizes[cue["style"]], caption["x"], caption["y"])
    context = design["context"]
    for i, cue in enumerate(plan["context"]):
        layer(f"context-{i}", "context", cue["start"], cue["end"], "Context", cue["text"], context["size"], context["x"], context["y"])
    return layers


def preview_layout(plan, font_path, output_time=0):
    """Return active text centres and logo top-left coordinates in canvas pixels."""
    moment = _number(output_time, "preview time")
    if not 0 <= moment < plan["duration_seconds"]:
        raise ValueError("미리보기 시각은 완성 영상의 0 이상, 길이 미만이어야 합니다.")
    design = normalize_design(plan.get("design"))
    layers = [item for item in _text_layers(plan, transform_captions(plan), font_path)
              if item["start"] <= moment < item["end"]]
    weight, italic = _font_style(font_path)
    return dict(canvas=[1080, 1920], panel=[0, 555, 1080, 810], logo=dict(design["logo"]),
                layers=layers, font_weight=weight, font_style="italic" if italic else "normal")


def _font_style(font_path):
    from PIL import ImageFont
    style = ImageFont.truetype(str(font_path), 32).getname()[1].lower()
    return (700 if any(word in style for word in ("bold", "black", "heavy", "demi")) else 400,
            "italic" in style or "oblique" in style)


def _subtitles(plan, captions, font_path):
    design = normalize_design(plan.get("design"))
    weight, italic = _font_style(font_path)
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""
    sizes = {"Title": design["title"]["size"], "Channel": design["channel"]["size"],
             "Caption": design["caption"]["size"], "Emphasis": design["caption"]["emphasis_size"],
             "Punchline": design["caption"]["punchline_size"], "Context": design["context"]["size"]}
    for name, size in sizes.items():
        color = "&H00AAFF00" if name in {"Emphasis", "Punchline"} else "&H00FFFFFF"
        if name == "Context":
            color = "&H00B8B8B8"
        outline, shadow = (4, 1) if name == "Caption" else (0, 0)
        header += (f"Style: {name},{plan['font_name']},{size},{color},&H000000FF,&H0017100C,"
                   f"&H80000000,{-1 if weight >= 700 else 0},{-1 if italic else 0},0,0,100,100,0,0,1,{outline},{shadow},5,40,40,0,1\n")
    header += "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    events = [f"Dialogue: 1,{_timestamp(item['start'], True)},{_timestamp(item['end'], True)},{item['style']},,0,0,0,,"
              f"{{\\pos({item['x']:g},{item['y']:g})\\fs{item['size']}}}{ass_text(item['text'])}"
              for item in _text_layers(plan, captions, font_path)]
    ass = header + "\n".join(events) + "\n"
    srt = "\n\n".join(f"{i}\n{_timestamp(c['start'])} --> {_timestamp(c['end'])}\n{c['text']}"
                        for i, c in enumerate(captions, 1)) + "\n"
    return ass, srt


def _visual_graph(plan, *, sequence="sequence", preview_time=None):
    c, logo = plan["crop"], normalize_design(plan.get("design"))["logo"]
    timing = f"setpts=PTS-STARTPTS+{preview_time:.6f}/TB," if preview_time is not None else ""
    return [
        f"[{sequence}]setsar=1,crop={c['width']}:{c['height']}:{c['x']}:{c['y']},"
        "scale=1080:810:flags=lanczos,pad=1080:1920:0:555:black[canvas]",
        f"[1:v]scale={logo['size']}:{logo['size']}:force_original_aspect_ratio=decrease,"
        f"pad={logo['size']}:{logo['size']}:(ow-iw)/2:(oh-ih)/2:color=black@0[logo]",
        f"[canvas][logo]overlay={logo['x']:g}:{logo['y']:g},{timing}"
        "ass=filename=captions.ass:fontsdir=fonts,format=yuv420p[v]",
    ]


def _filter_graph(plan, has_audio):
    n, first = len(plan["keep"]), plan["keep"][0][0]
    parts = [f"[0:v]split={n}" + "".join(f"[vin{i}]" for i in range(n))]
    if has_audio:
        parts.append(f"[0:a]asplit={n}" + "".join(f"[ain{i}]" for i in range(n)))
    for i, (a, b) in enumerate(plan["keep"]):
        parts.append(f"[vin{i}]trim=start={a-first:.6f}:end={b-first:.6f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            parts.append(f"[ain{i}]atrim=start={a-first:.6f}:end={b-first:.6f},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d=0.006,afade=t=out:st={b-a-.006:.6f}:d=0.006[a{i}]")
    inputs = "".join(f"[v{i}][a{i}]" if has_audio else f"[v{i}]" for i in range(n))
    parts.append(inputs + f"concat=n={n}:v=1:a={int(has_audio)}[sequence]" + ("[sound]" if has_audio else ""))
    if not has_audio:
        parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={plan['duration_seconds']:.6f}[sound]")
    parts.append("[sequence]fps=30[timed]")
    parts.extend(_visual_graph(plan, sequence="timed"))
    # loudnorm can emit NaNs for generated digital silence on some FFmpeg builds.
    normalization = "loudnorm=I=-16:TP=-1.5:LRA=11," if has_audio else ""
    parts.extend([
        f"[sound]{normalization}aresample=48000,"
        f"afade=t=out:st={max(0, plan['duration_seconds']-.10):.6f}:d=0.10[a]",
    ])
    return ";".join(parts)


def _gallery(rows):
    cards = []
    for row in rows:
        title = html.escape(" ".join(row["title_lines"]))
        video, subtitles = html.escape(row["video"], quote=True), html.escape(row["captions_file"], quote=True)
        cards.append(f'<article><h2>{title}</h2><p>{row["duration_seconds"]:.1f}초</p>'
                     f'<video controls playsinline preload="metadata" src="{video}"></video>'
                     f'<p><a href="{video}" download>MP4</a> · <a href="{subtitles}" download>한국어 자막</a></p></article>')
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>쇼츠 비교</title><style>body{background:#141414;color:#eee;font:16px sans-serif;max-width:1000px;margin:32px auto;padding:0 20px}'
            'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px}article{background:#222;padding:20px;border-radius:12px}'
            'h2{font-size:20px}video{display:block;width:100%;max-width:280px;aspect-ratio:9/16;margin:auto;background:#000}a{color:#7ce9c1}</style>'
            '<h1>쇼츠 비교</h1><p>편집 계획의 원본 시간순으로 구성한 로컬 렌더입니다. 자막과 장면은 게시 전에 확인하세요.</p><main>'
            + "".join(cards) + '</main></html>')


def load_render_plan(plan_path):
    """Read and validate a plan and its local assets without mutating any files."""
    plan_path = Path(plan_path).resolve()
    raw = read(plan_path)
    if not isinstance(raw, dict):
        raise ValueError("편집 계획은 JSON 객체여야 합니다.")
    files = {key: _local_file(raw.get(key), plan_path.parent, key) for key in ("source", "logo", "font")}
    source_info = probe(files["source"])
    videos = [s for s in source_info.get("streams", []) if s.get("codec_type") == "video"]
    if not videos:
        raise ValueError("source에 영상 스트림이 없습니다.")
    video = videos[0]
    if any(float(s.get("rotation", 0)) % 360 for s in video.get("side_data_list", [])) or float(video.get("tags", {}).get("rotate", 0)) % 360:
        raise ValueError("회전 메타데이터가 있는 원본은 먼저 회전을 적용한 영상으로 변환하세요.")
    plan = validate_plan(raw, source_duration=float(video.get("duration") or source_info["format"]["duration"]),
                         source_width=video["width"], source_height=video["height"])
    return plan, files, source_info


def _source_time(plan, output_time):
    cursor = 0.0
    for start, end in plan["keep"]:
        length = end - start
        if output_time < cursor + length:
            return start + output_time - cursor
        cursor += length
    raise ValueError("미리보기 시각은 완성 영상의 길이 미만이어야 합니다.")


def render_preview(plan_path, output_path, time_seconds=0):
    """Render one PNG using the same crop, logo, font and ASS as the MP4 renderer.

    ``time_seconds`` is on the edited output timeline, not the original source.
    Existing preview files survive validation and FFmpeg failures.
    """
    require_tools()
    plan_path, output = Path(plan_path).resolve(), Path(output_path).resolve()
    if output.suffix.lower() != ".png":
        raise ValueError("미리보기 출력은 .png 파일이어야 합니다.")
    plan, files, _ = load_render_plan(plan_path)
    moment = _number(time_seconds, "preview time")
    layout = preview_layout(plan, files["font"], moment)
    source_time = _source_time(plan, moment)
    protected = [plan_path, *files.values()]
    if output in protected or (output.exists() and any(output.samefile(p) for p in protected)):
        raise ValueError("출력 파일이 편집 계획 또는 입력 파일과 같습니다. 다른 출력 파일을 지정하세요.")
    ass, _ = _subtitles(plan, transform_captions(plan), files["font"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".preview-", dir=output.parent) as temporary:
        stage = Path(temporary)
        (stage / "fonts").mkdir()
        shutil.copy2(files["font"], stage / "fonts" / ("provided" + files["font"].suffix))
        (stage / "captions.ass").write_text(ass, encoding="utf-8-sig")
        args = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y", "-ss",
                f"{source_time-plan['source_start']:.6f}", "-i", str(files["source"]), "-i", str(files["logo"]),
                "-filter_complex", ";".join(_visual_graph(plan, sequence="0:v", preview_time=moment)),
                "-map", "[v]", "-an", "-frames:v", "1", "-fps_mode", "passthrough", "-update", "1",
                str(stage / "preview.png")]
        result = subprocess.run(args, cwd=stage, capture_output=True, encoding="utf-8", errors="replace")
        if result.returncode or not (stage / "preview.png").is_file():
            raise RuntimeError(f"FFmpeg 미리보기 실패 (이전 출력 보존):\n{result.stderr[-5000:]}")
        from PIL import Image
        with Image.open(stage / "preview.png") as image:
            if image.size != (1080, 1920):
                raise RuntimeError("미리보기 해상도 검증에 실패했습니다. 이전 출력은 보존했습니다.")
            image.verify()
        (stage / "preview.png").replace(output)
    return {**layout, "width": 1080, "height": 1920, "time_seconds": moment, "source_time": source_time}


def render_plan(plan_path, output_dir, *, gallery=False):
    """Render one plan, then upsert its slug in output_dir/manifest.json.

    Preparation or FFmpeg failure preserves existing outputs. Calls to the same
    output directory must be sequential. Each published file is replaced atomically.
    """
    require_tools()
    plan_path, output = Path(plan_path).resolve(), Path(output_dir).resolve()
    gallery = bool(gallery or (output / "index.html").exists())
    plan, files, source_info = load_render_plan(plan_path)
    slug = plan["slug"]
    names = [f"{slug}.mp4", f"{slug}.ass", f"{slug}.ko.srt", f"{slug}.json", "manifest.json"]
    if gallery:
        names.append("index.html")
    protected = [plan_path, *files.values()]
    for name in names:
        destination = output / name
        if destination.resolve() in protected or (destination.exists() and any(destination.samefile(p) for p in protected)):
            raise ValueError("출력 파일이 편집 계획 또는 입력 파일과 같습니다. 다른 출력 폴더를 지정하세요.")
    prior = read(output / "manifest.json") if (output / "manifest.json").exists() else {"schema_version": 1, "renders": []}
    if not isinstance(prior, dict) or prior.get("schema_version") != 1 or not isinstance(prior.get("renders"), list):
        raise ValueError("기존 manifest.json 형식이 다릅니다. 새 출력 폴더를 사용하세요.")
    rows = prior["renders"]
    for row in rows:
        if not isinstance(row, dict) or not SLUG.fullmatch(str(row.get("slug", ""))) or \
                row.get("video") != f"{row['slug']}.mp4" or row.get("captions_file") != f"{row['slug']}.ko.srt":
            raise ValueError("기존 manifest.json에 잘못된 렌더 항목이 있습니다.")
    captions = transform_captions(plan)
    ass, srt = _subtitles(plan, captions, files["font"])
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".render-", dir=output) as temporary:
        stage = Path(temporary)
        (stage / "fonts").mkdir()
        shutil.copy2(files["font"], stage / "fonts" / ("provided" + files["font"].suffix))
        (stage / "captions.ass").write_text(ass, encoding="utf-8-sig")
        (stage / f"{slug}.ass").write_text(ass, encoding="utf-8-sig")
        (stage / f"{slug}.ko.srt").write_text(srt, encoding="utf-8-sig")
        has_audio = any(s.get("codec_type") == "audio" for s in source_info["streams"])
        args = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y", "-ss",
                f"{plan['keep'][0][0]-plan['source_start']:.6f}", "-i", str(files["source"]), "-i", str(files["logo"]),
                "-t", f"{plan['duration_seconds']:.6f}", "-filter_complex", _filter_graph(plan, has_audio),
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                "-metadata", "title=" + " ".join(plan["title_lines"]), str(stage / f"{slug}.mp4")]
        result = subprocess.run(args, cwd=stage, capture_output=True, encoding="utf-8", errors="replace")
        if result.returncode:
            raise RuntimeError(f"FFmpeg 렌더 실패 (이전 출력 보존):\n{result.stderr[-5000:]}")
        rendered = probe(stage / f"{slug}.mp4")
        streams = rendered.get("streams", [])
        actual_duration = float(rendered["format"]["duration"])
        if abs(actual_duration - plan["duration_seconds"]) > max(.15, 1 / 30 * len(plan["keep"])) or \
                not any(s.get("width") == 1080 and s.get("height") == 1920 for s in streams) or \
                not any(s.get("codec_type") == "audio" for s in streams):
            raise RuntimeError("렌더 결과의 길이·해상도·오디오 검증에 실패했습니다. 이전 출력은 보존했습니다.")
        row = {**plan, "source": str(files["source"]), "plan_file": str(plan_path),
               "video": f"{slug}.mp4", "captions_file": f"{slug}.ko.srt", "ass_file": f"{slug}.ass",
               "rendered_captions": captions, "actual_duration_seconds": actual_duration,
               "source_has_audio": has_audio, "human_approved": False,
               "layout": {"canvas": [1080, 1920], "panel": [0, 555, 1080, 810], "fps": 30},
               "assets": {k: str(v) for k, v in files.items() if k != "source"}}
        next_rows = [row if r["slug"] == slug else r for r in rows]
        if not any(r["slug"] == slug for r in rows):
            next_rows.append(row)
        save(stage / f"{slug}.json", row)
        save(stage / "manifest.json", {"schema_version": 1, "renders": next_rows})
        if gallery:
            (stage / "index.html").write_text(_gallery(next_rows), encoding="utf-8")
        for name in names:
            (stage / name).replace(output / name)
    return row
