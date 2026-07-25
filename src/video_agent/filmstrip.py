"""Overview filmstrip: n low-res frames tiled into ONE image.

Scan tool, not a verification tool (small cells lose the detail speaker/text
checks need — that's what full-res capture is for). One vision read decides
*which* moments deserve a full-res capture. Pattern validated by
browser-use/video-use (decision-point filmstrip) and SlowFast's
low-res-many/high-res-one split.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

from .image_provenance import (
    ImageEventInput,
    append_image_provenance,
    cause_identity,
)
from .image_validation import is_decodable_image
from .proc import run
from .workspace import Workspace

CELL_WIDTH = 320
MAX_CELL_GAP = 7.0     # measured (docs/eval): sparser grids miss 1-3s inserts
MAX_TILE_SPAN = 112.0  # 16 cells x 7s — one tile's densest coverage
FILMSTRIP_RENDER_VERSION = 1


def plan_windows(
    duration: float, start: float = 0.0, end: float | None = None
) -> list[tuple[float, float, int]]:
    """Split [start, end] into density-rule windows: (start, end, n) each
    with span <= 112s and cell gap <= 7s (n capped at 16)."""
    end = duration if end is None else min(end, duration)
    span = end - start
    if span <= 0:
        raise ValueError(f"invalid span: start={start} end={end}")
    k = math.ceil(span / MAX_TILE_SPAN)
    win = span / k
    return [
        (round(start + i * win, 3),
         round(start + (i + 1) * win, 3),
         min(16, max(1, math.ceil(win / MAX_CELL_GAP))))
        for i in range(k)
    ]


def make_filmstrip(
    ws: Workspace,
    start: float,
    end: float,
    n: int = 9,
    cols: int = 3,
) -> tuple[Path, list[float]]:
    """Tile n evenly spaced frames from [start, end] into one jpg.

    Returns (path, cell-midpoint timestamps in cell order).
    """
    duration = ws.manifest["duration"]
    if not 0 <= start < end:
        raise ValueError(f"invalid span: start={start} end={end}")
    if end > duration + 0.5:
        raise ValueError(f"end {end}s beyond video duration {duration:.2f}s")
    if not 1 <= n <= 16:
        raise ValueError("n must be 1..16")
    if not 1 <= cols <= 16:
        raise ValueError("cols must be 1..16")

    mids = [start + (i + 0.5) * (end - start) / n for i in range(n)]

    def _human(t: float) -> str:
        m, sec = divmod(int(t), 60)
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m}m{sec:02d}s"

    # 캐시 키 = 기계 접두(파라미터 인코딩), 사람 구간은 표시 접미 —
    # 브레드크럼·그래프에서 파일만 봐도 어느 구간 조망인지 읽힌다
    base = (
        f"strip_{round(start * 1000):09d}_{round(end * 1000):09d}"
        f"_n{n}_c{cols}_v{FILMSTRIP_RENDER_VERSION}"
    )
    candidates = sorted(ws.frames_dir.glob(f"{base}*.jpg"))
    cached = next((c for c in candidates if is_decodable_image(c)), None)
    if cached is not None:
        dest = cached
    elif candidates:
        dest = candidates[0]  # 손상 캐시 자리 재사용(이름 증식 방지)
    else:
        dest = ws.frames_dir / (
            f"{base}-조망{_human(start)}~{_human(end)}.jpg")
    rounded_mids = [round(mid, 3) for mid in mids]
    cause_context: dict[str, object] = {
        "span": [round(start, 3), round(end, 3)],
        "timestamps": rounded_mids,
        "cells": n,
        "cols": cols,
        "render_version": FILMSTRIP_RENDER_VERSION,
    }
    provenance: list[ImageEventInput] = [{
        "path": f"frames/{dest.name}",
        "kind": "filmstrip",
        "cause_type": "overview",
        "cause_id": cause_identity(ws, "overview", cause_context),
        "reason": "filmstrip-overview",
        "span": [round(start, 3), round(end, 3)],
        "timestamps": rounded_mids,
        "cells": n,
        "cols": cols,
        "render_version": FILMSTRIP_RENDER_VERSION,
    }]
    if cached is not None or is_decodable_image(dest):
        append_image_provenance(ws, provenance)
        return dest, mids

    rows = math.ceil(n / cols)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for t in mids:
        cmd += ["-ss", f"{t:.3f}", "-i", str(ws.video)]
    # trim=end_frame=1: each -ss input is a full stream from its seek point;
    # without the trim, concat+tile grabs n consecutive frames from the FIRST
    # input and every cell shows the same moment.
    scaled = "".join(
        f"[{i}:v]trim=end_frame=1,scale={CELL_WIDTH}:-2[c{i}];" for i in range(n)
    )
    chain = "".join(f"[c{i}]" for i in range(n))
    with tempfile.NamedTemporaryFile(
        prefix=f".{dest.stem}.", suffix=dest.suffix,
        dir=ws.frames_dir, delete=False,
    ) as temporary_file:
        temporary = Path(temporary_file.name)
    try:
        cmd += [
            "-filter_complex",
            f"{scaled}{chain}concat=n={n}:v=1[s];[s]tile={cols}x{rows}",
            "-frames:v", "1", "-q:v", "4", str(temporary),
        ]
        res = run(cmd, capture_output=True, text=True)
        if res.returncode != 0 or not is_decodable_image(temporary):
            raise RuntimeError(
                f"ffmpeg filmstrip failed ({res.returncode}): {res.stderr.strip()}"
            )
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)
    append_image_provenance(ws, provenance)
    return dest, mids
