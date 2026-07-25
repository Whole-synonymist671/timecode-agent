"""ffprobe wrapper: duration/size/fps for a video file."""

from __future__ import annotations

import json
from pathlib import Path

from .proc import run


def probe(video: Path) -> dict:
    video = Path(video)
    if not video.is_file():
        raise FileNotFoundError(f"video not found: {video}")
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-show_chapters", str(video),
    ]
    res = run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({res.returncode}): {res.stderr.strip()}")
    data = json.loads(res.stdout)
    vstream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    if vstream is None:
        raise RuntimeError(f"no video stream in {video}")
    num, _, den = vstream.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    # container chapters (mp4 chapter atoms / yt-dlp --embed-chapters):
    # a free semantic pre-map — each chapter is a checkpoint draft the agent
    # gets without spending a single frame or transcript read
    chapters = [
        {
            "start": round(float(c.get("start_time", 0.0)), 3),
            "end": round(float(c.get("end_time", 0.0)), 3),
            "title": (c.get("tags") or {}).get("title", ""),
        }
        for c in data.get("chapters", [])
    ]
    meta = {
        "duration": float(data["format"]["duration"]),
        "width": int(vstream["width"]),
        "height": int(vstream["height"]),
        "fps": fps,
        "has_audio": any(
            s.get("codec_type") == "audio" for s in data.get("streams", [])
        ),
    }
    # 색 신호(휘도·색 히스토그램·색 교차확인)의 해석 전제 — HDR/SDR·
    # range 혼동 방지를 위해 관측 조건을 manifest에 남긴다
    color = {
        key: vstream[key]
        for key in ("pix_fmt", "color_space", "color_transfer",
                    "color_primaries", "color_range")
        if vstream.get(key)
    }
    if color:
        meta["color"] = color
    if chapters:
        meta["chapters"] = chapters
    creation = (data["format"].get("tags") or {}).get("creation_time")
    if creation:
        meta["creation_time"] = creation
    return meta
