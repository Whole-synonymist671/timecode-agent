"""Clip extraction for editing handoff."""

from __future__ import annotations

import sys
from pathlib import Path

from .proc import run
from .workspace import Workspace


def extract_clip(
    ws: Workspace,
    start: float,
    end: float,
    name: str | None = None,
    accurate: bool = False,
    hw: str | None = None,
) -> Path:
    duration = ws.manifest["duration"]
    if not 0 <= start < end:
        raise ValueError(f"invalid clip range: start={start} end={end}")
    if end > duration + 0.5:
        raise ValueError(f"end {end}s beyond video duration {duration:.2f}s")
    if hw and not accurate:
        raise ValueError("--hw requires --accurate (copy mode never re-encodes)")
    if hw and hw not in ("h264", "hevc"):
        raise ValueError(f"hw must be h264 or hevc, got {hw!r}")
    if hw and sys.platform != "darwin":
        raise ValueError("--hw uses VideoToolbox and is available only on macOS")
    dest = ws.clips_dir / (
        name or f"clip_{round(start * 1000):09d}_{round(end * 1000):09d}.mp4"
    )
    hwaccel: list[str] = []
    if accurate and hw:
        # VideoToolbox reduces CPU load on supported macOS hardware and is
        # intended for previews or concurrent real-time work.
        hwaccel = ["-hwaccel", "videotoolbox"]
        codec = [
            "-c:v",
            f"{hw}_videotoolbox",
            "-q:v",
            "55",
            "-c:a",
            "aac_at",
        ]
        if hw == "hevc":
            codec += ["-tag:v", "hvc1"]
    elif accurate:
        codec = ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac"]
    else:
        # Fast keyframe-aligned trim; boundaries may shift to nearest keyframe.
        codec = ["-c", "copy"]
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", *hwaccel,
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(ws.video),
        *codec, str(dest),
    ]
    res = run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not dest.is_file():
        raise RuntimeError(
            f"ffmpeg clip failed ({res.returncode}): {res.stderr.strip()}"
        )
    return dest
