"""Color cross-check for luma scene detections (조명 오탐 감별).

luma 기반 ffmpeg scene 점수는 조명 플래시를 컷으로 오탐한다(실측: 지하철
캠 82건 전량 오탐). 밝기를 배제한 H-S(색상·채도) 히스토그램 거리는 조명
변화에 견고하다 — 단 단독 대체는 불가(톤 통일 팔레트에서 실제 컷 미탐,
게임 트레일러 실측 3/3)하므로 **luma 검출의 교차 확인**에만 쓴다: 색 구성 변화까지
동반한 검출만 확인(confirmed)으로 승격하고 나머지는 조명 오탐 의심으로
표시한다. placement 신호의 정밀도 보강이며 selection은 여전히 에이전트 몫.
"""

from __future__ import annotations

from pathlib import Path

from .proc import run

SCAN_FPS = 8.0
SCAN_WIDTH = 160
CONFIRM_THRESHOLD = 0.5   # Hellinger — 실측 경계(조명 0.15~0.34 vs 컷 0.5+)
MATCH_WINDOW = 0.75       # 검출 시점 주변에서 색 변화를 찾는 창(초)


def hs_histogram(rgb) -> "object":
    """RGB float 프레임(H,W,3, 0~1) → H-S 2D 히스토그램(L1 정규화, 1152)."""
    import numpy as np

    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = rgb.max(-1)
    delta = mx - rgb.min(-1)
    s = np.where(mx > 0, delta / np.maximum(mx, 1e-8), 0.0)
    h = np.zeros_like(mx)
    mask = (delta > 1e-8) & (mx == r)
    h[mask] = ((g - b)[mask] / delta[mask]) % 6
    mask = (delta > 1e-8) & (mx == g)
    h[mask] = (b - r)[mask] / delta[mask] + 2
    mask = (delta > 1e-8) & (mx == b)
    h[mask] = (r - g)[mask] / delta[mask] + 4
    hist, _, _ = np.histogram2d(
        (h / 6.0).ravel(), s.ravel(), bins=[36, 32], range=[[0, 1], [0, 1]])
    total = hist.sum()
    return (hist / total).ravel() if total else hist.ravel()


def hellinger(p, q) -> float:
    """L1 정규화 분포 간 Hellinger 거리(0=동일, 1=완전 분리)."""
    import numpy as np

    return float(np.sqrt(max(0.0, 1.0 - np.sqrt(p * q).sum())))


def color_distance_series(video: Path) -> list[tuple[float, float]]:
    """[(t, 직전 샘플과의 H-S 거리), ...] — 저해상 단일 디코드 패스."""
    import numpy as np

    cmd = [
        "ffmpeg", "-loglevel", "error", "-i", str(video),
        "-vf", f"fps={SCAN_FPS},scale={SCAN_WIDTH}:-2",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    res = run(cmd, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg color pass failed ({res.returncode})")
    raw = np.frombuffer(res.stdout, dtype=np.uint8)
    # 높이는 scale=-2 산출이라 프레임 크기로 역산한다
    probe = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0",
                 str(video)], capture_output=True, text=True)
    src_w, src_h = (int(x) for x in probe.stdout.strip().split(",")[:2])
    height = max(2, round(SCAN_WIDTH * src_h / src_w / 2) * 2)
    frame_bytes = SCAN_WIDTH * height * 3
    n = len(raw) // frame_bytes
    frames = raw[: n * frame_bytes].reshape(n, height, SCAN_WIDTH, 3)
    frames = frames.astype(np.float32) / 255.0
    series: list[tuple[float, float]] = []
    prev = None
    for i in range(n):
        hist = hs_histogram(frames[i])
        if prev is not None:
            series.append((i / SCAN_FPS, hellinger(prev, hist)))
        prev = hist
    return series


def color_check_scenes(
    scenes: list[dict],
    series: list[tuple[float, float]],
    *,
    threshold: float = CONFIRM_THRESHOLD,
    window: float = MATCH_WINDOW,
) -> list[dict]:
    """각 luma 검출에 color(주변 최대 H-S 거리)·color_confirmed를 부여."""
    checked = []
    for sc in scenes:
        near = [d for t, d in series if abs(t - sc["t"]) <= window]
        dist = max(near) if near else 0.0
        checked.append({
            **sc,
            "color": round(dist, 3),
            "color_confirmed": dist >= threshold,
        })
    return checked
