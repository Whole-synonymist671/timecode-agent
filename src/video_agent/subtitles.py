"""Reuse subtitles the video already ships with (sidecar or embedded).

When captions exist they are faster and usually more accurate than Whisper —
and we keep the cue timestamps instead of flattening cues to plain text,
because the whole pipeline hangs off them.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .proc import run

Cue = tuple[float, float, str]

_TIME = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{3})"
_CUE_LINE = re.compile(rf"{_TIME}\s*-->\s*{_TIME}")


def _ts(h, m, s, ms) -> float:
    return (int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0)


def parse_cues(raw: str) -> list[Cue]:
    """Parse .srt/.vtt text into [(start, end, text), ...]."""
    cues: list[Cue] = []
    current: tuple[float, float] | None = None
    lines: list[str] = []

    def flush():
        nonlocal current, lines
        if current and lines:
            cues.append((current[0], current[1], " ".join(lines)))
        current, lines = None, []

    for ln in raw.splitlines():
        s = ln.strip().lstrip("﻿")
        m = _CUE_LINE.search(s)
        if m:
            flush()
            g = m.groups()
            current = (_ts(*g[:4]), _ts(*g[4:]))
        elif not s or s.startswith(("WEBVTT", "NOTE")) or s.isdigit():
            if not s:
                flush()
        elif current is not None:
            text = re.sub(r"<[^>]+>", "", s).strip()
            if text:
                lines.append(text)
    flush()
    return cues


def _has_subtitle_stream(video: Path) -> bool:
    res = run(
        ["ffprobe", "-v", "error", "-select_streams", "s",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    )
    return bool(res.stdout.strip())


def find_subtitle_cues(video: Path) -> list[Cue] | None:
    """Sidecar .srt/.vtt next to the video first, then embedded stream."""
    video = Path(video)
    for ext in (".srt", ".vtt"):
        sidecar = video.with_suffix(ext)
        if sidecar.is_file():
            cues = parse_cues(sidecar.read_text(encoding="utf-8",
                                                errors="ignore"))
            if cues:
                return cues
    if _has_subtitle_stream(video):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "embedded.srt"
            run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                 "-map", "0:s:0", str(out)],
                capture_output=True, text=True,
            )
            if out.is_file():
                cues = parse_cues(out.read_text(encoding="utf-8",
                                                errors="ignore"))
                if cues:
                    return cues
    return None


def _word_overlap(prev_words: list[str], cur_words: list[str]) -> int:
    """Longest suffix of prev that is a prefix of cur (in words)."""
    for k in range(min(len(prev_words), len(cur_words)), 0, -1):
        if prev_words[-k:] == cur_words[:k]:
            return k
    return 0


def dedup_rolling_cues(
    cues: list[tuple[float, float, str]], max_span: float = 12.0
) -> list[tuple[float, float, str]]:
    """Normalize YouTube auto-caption rolling: zero-duration fragments and
    cumulative repeats merge back into sentence-level cues.

    Manual subtitles have no suffix/prefix overlap, so this is a no-op for
    them (measured: 0 overlapping pairs on human-made tracks vs 53/53 on an
    auto track). max_span stops a rolling chain from merging into one blob.
    """
    out: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        words = text.split()
        if out:
            ps, pe, ptext = out[-1]
            pwords = ptext.split()
            k = _word_overlap(pwords, words)
            if k and (end - ps) <= max_span:
                merged = " ".join(pwords + words[k:])
                out[-1] = (ps, max(pe, end), merged)
                continue
            if k == len(words):  # tail fully contained but span exceeded
                out[-1] = (ps, max(pe, end), ptext)
                continue
        out.append((start, end, text))
    return out
