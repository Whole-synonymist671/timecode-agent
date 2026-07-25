"""Legibility-aware end-card placement.

The fixed `duration - 1.0` rule places one frame near the tail and hopes it
is readable. Two benches caught the same failure: the frame was held but the
card was not legible at that instant (J08), and on another video the exact
frame did not decode and needed a re-seek (M08).

This module keeps the placement deterministic while making it legibility
aware: sample a few tail candidates, drop the ones that do not decode, and
prefer the most **temporally static** one. An end card holds still for
seconds while the footage before it (and any fade into it) keeps changing,
so neighbour-to-neighbour SSIM separates "card fully shown" from "mid-fade
or still playing". Edge energy (sharpness) was rejected as the signal:
noisy footage scores higher than a clean flat card, which inverts the pick.

Uses the same deterministic ffmpeg SSIM probe as the join gate — no OCR pass
is required even though the macOS runtime includes the OCR backend.
"""

from __future__ import annotations

from .boundary_eval import frame_luma_range, frame_ssim
from .workspace import Workspace

_WINDOW = 2.5      # 꼬리 탐색 창(초)
_SAMPLES = 5       # 창 안 후보 수
_TAIL_MARGIN = 0.2  # 마지막 프레임 디코드 실패를 피하는 여유
_MIN_LUMA_RANGE = 16.0  # 이보다 평평하면 검은 화면·단색 페이드로 본다


def endcard_candidates(
    duration: float,
    *,
    window: float = _WINDOW,
    samples: int = _SAMPLES,
    limit: float | None = None,
) -> list[float]:
    """꼬리 창을 균등 분할한 후보 — 범위 클램프, 오름차순, 중복 제거.

    `limit`은 호출자가 허용하는 마지막 시점이다(예: keyframes의 --end).
    이를 넘겨 고른 시점은 호출자 쪽에서 조용히 버려져 엔드카드 후보가
    통째로 사라지므로, 창 자체를 미리 좁힌다.
    """
    if duration <= 0 or samples < 1:
        return []
    last = max(duration - _TAIL_MARGIN, 0.0)
    if limit is not None:
        last = min(last, limit)
    if last < 0:
        return []
    first = max(last - window, 0.0)
    if samples == 1:
        return [round(last, 3)]
    step = (last - first) / (samples - 1)
    return sorted({
        round(min(first + step * index, last), 3)
        for index in range(samples)
    })


def endcard_pick(
    ws: Workspace,
    *,
    window: float = _WINDOW,
    samples: int = _SAMPLES,
    limit: float | None = None,
) -> float | None:
    """가장 정적인(=카드가 온전히 떠 있는) 꼬리 시점 — 디코드 실패는 제외.

    각 후보의 안정도 = 인접 후보와의 SSIM 최댓값. 동점은 늦은 쪽(카드는
    보통 끝까지 유지된다)으로 결정적으로 고정한다. 후보 프레임은 캡처
    캐시에 남아 이어지는 검증에 재사용된다.

    **빈 화면은 먼저 배제한다.** 카드 뒤에 정지된 검은 꼬리가 붙는 편집은
    흔한데, 그러면 카드 쌍도 블랙 쌍도 SSIM 1.0이라 늦은 쪽 우선 규칙이
    하필 블랙을 고른다 — 가독성 옵션의 목적을 정확히 무산시킨다.
    """
    duration = float(ws.manifest.get("duration") or 0.0)
    candidates = endcard_candidates(
        duration, window=window, samples=samples, limit=limit
    )
    legible = [
        t for t in candidates
        if (frame_luma_range(ws, t) or 0.0) >= _MIN_LUMA_RANGE
    ]
    # 전부 평평하면 판정할 근거가 없다 — 호출자의 고정 규칙으로 되돌린다.
    candidates = legible
    if len(candidates) < 2:
        return candidates[0] if candidates else None

    pair_ssim: list[float | None] = []
    for left, right in zip(candidates, candidates[1:]):
        try:
            pair_ssim.append(frame_ssim(ws, left, right))
        except (RuntimeError, ValueError):
            pair_ssim.append(None)  # 디코드 실패 쌍(M08 재시크 사례)

    scored: list[tuple[float, float]] = []
    for index, t in enumerate(candidates):
        neighbours = [
            value
            for value in (
                pair_ssim[index - 1] if index > 0 else None,
                pair_ssim[index] if index < len(pair_ssim) else None,
            )
            if value is not None
        ]
        if neighbours:
            scored.append((max(neighbours), t))
    if not scored:
        return None
    return max(scored)[1]
