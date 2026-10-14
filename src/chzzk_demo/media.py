from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def run(args):
    subprocess.run([str(x) for x in args], check=True)


def require_tools():
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(name):
            raise RuntimeError(f"{name}가 PATH에 없습니다. FFmpeg 설치 후 터미널을 다시 여세요.")


def probe(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                             "-of", "json", str(path)], check=True, capture_output=True, encoding="utf-8")
    return json.loads(result.stdout)


def extract(source, output, start, duration, audio=False):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.stem + ".partial" + output.suffix)
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", str(start),
            "-i", source, "-t", str(duration)]
    if audio:
        args += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
    else:
        args += ["-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=-2:480",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "25", "-c:a", "aac",
                 "-movflags", "+faststart"]
    run(args + [temp])
    if float(probe(temp)["format"]["duration"]) <= 0:
        raise RuntimeError("출력 길이가 0입니다.")
    temp.replace(output)


def audio_levels(path, start):
    import wave
    import numpy as np
    rows = []
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError("16-bit mono PCM audio required")
        rate = wav.getframerate()
        index = 0
        while data := wav.readframes(rate):
            samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
            rms = float(np.sqrt(np.mean(samples * samples)))
            rows.append({"time": start + index, "db": 20 * float(np.log10(max(rms, 1e-7)))})
            index += 1
    return rows
