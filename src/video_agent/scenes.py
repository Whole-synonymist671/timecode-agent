"""Scene-change placement signal (visual counterpart to audio highlights).

Metadata-only ffmpeg pass — per-frame scene scores without extracting a
single image. Complements transcript (semantic) and audio energy signals for
picking verification points, especially in speech-free stretches. Adaptive
mode (rolling-average-relative threshold) catches gradual morphs a fixed
threshold misses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .fsio import write_text_atomic
from .proc import run
from .workspace import Workspace


def scene_scores(video: Path) -> list[tuple[float, float]]:
    """[(t_seconds, scene_score), ...] for every frame, via one null-sink pass."""
    cmd = [
        "ffmpeg", "-loglevel", "error", "-i", str(video),
        "-vf", "select='gte(scene,0)',metadata=print:file=-",
        "-an", "-f", "null", "-",
    ]
    res = run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"ffmpeg scene pass failed ({res.returncode}): {res.stderr.strip()}"
        )
    scores: list[tuple[float, float]] = []
    t = None
    for line in res.stdout.splitlines():
        if line.startswith("frame:"):
            t = None
            for field in line.split():
                if field.startswith("pts_time:"):
                    t = float(field.removeprefix("pts_time:"))
        elif line.startswith("lavfi.scene_score=") and t is not None:
            scores.append((t, float(line.partition("=")[2])))
    return scores


def detect_scenes(
    scores: list[tuple[float, float]],
    threshold: float = 0.3,
    adaptive: bool = False,
    window_s: float = 2.0,
    mult: float = 3.0,
    min_content: float = 0.04,
    min_gap: float = 1.0,
) -> list[dict]:
    """Scene-change moments; within min_gap only the strongest survives."""
    if not scores:
        return []
    hits: list[dict] = []
    rolling: list[float] = []
    for i, (t, s) in enumerate(scores):
        hit = s >= threshold
        if adaptive and not hit and rolling:
            avg = sum(rolling) / len(rolling)
            hit = s >= min_content and s >= avg * mult
        if hit:
            hits.append({"t": round(t, 3), "score": round(s, 3)})
        rolling.append(s)
        # rolling window in seconds -> approximate frame count from spacing
        if len(scores) > 1:
            dt = scores[1][0] - scores[0][0] or 0.04
            if len(rolling) > max(1, round(window_s / dt)):
                rolling.pop(0)

    merged: list[dict] = []
    for h in hits:
        if merged and h["t"] - merged[-1]["t"] < min_gap:
            if h["score"] > merged[-1]["score"]:
                merged[-1] = h
        else:
            merged.append(h)
    return merged


def compute_scenes(
    ws: Workspace, threshold: float = 0.3, adaptive: bool = False
) -> list[dict]:
    scenes = detect_scenes(scene_scores(ws.video), threshold=threshold,
                           adaptive=adaptive)
    if not scenes:
        print("note: no scene changes above threshold", file=sys.stderr)
    write_text_atomic(ws.root / "scenes.json",
                      json.dumps(scenes, ensure_ascii=False, indent=1))
    return scenes
