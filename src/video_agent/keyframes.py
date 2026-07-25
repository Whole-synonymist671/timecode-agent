"""Budget-aware keyframe selection — training-free, query-blind.

조망 후보(장면전환·버스트·저신뢰 구간)가 캡처 예산을 초과할 때, 결정적
신호 가중과 시간축 커버리지 이득을 균형해 예산 내 부분집합을 고른다.
placement 전용: "어디를 볼 것인가"만 산출하고, 프레임이 무엇인지는
에이전트가 판단한다.

AKS(arXiv:2502.21271)의 relevance+coverage 목적함수에서 **coverage 항만**
가져왔다. AKS의 relevance는 CLIP/BLIP ITM으로 질의-프레임 유사도를 재지만,
여기서는 그 자리를 결정적 신호 가중(scene/burst/저신뢰)이 대신한다 —
모델 호출도 질의 의존성도 없다. 대가는 명확하다: AKS가 관측한 대로
단일 순간 질의에서는 relevance 항이 우세한데, 이 selector는 그 이득을
구조적으로 얻지 못한다. 질의 조건부 선별은 두 번 실측해 프로덕션
NO-GO였다(docs/eval/2026-07-24-query-routed-*).

실행 표면은 ``va keyframes`` 하나다 — 병렬 모듈 CLI를 두면 플래그·타임코드
파싱·에러 래핑이 표면마다 갈린다(2026-07-25 감사에서 실측 제거).
"""

from __future__ import annotations

import math
from typing import TypedDict

from .checkpoints import load_checkpoints
from .transcript_segments import TranscriptSegment, load_transcript_segments
from .workspace import Workspace, load_json

# 신호별 기본 가중 — scenes 점수(0~1)는 가산, 나머지는 고정 프라이어.
_W_SCENE_BASE = 0.5
_W_BURST_MID = 0.7
_W_BURST_EDGE = 0.55
_W_LOWCONF_SEG = 0.6
_W_ENDCARD = 0.65
_UNCERTAIN_CONF = 0.7  # 체크포인트 confidence 미만이면 검증 후보
_MERGE_EPS = 0.5  # 이 간격 이내 후보는 동일 시점으로 병합


class Candidate(TypedDict):
    t: float
    weight: float
    source: str


class RejectedCandidate(TypedDict):
    t: float
    weight: float
    source: str
    reason: str  # "min_gap_displaced" | "budget_exhausted"
    displaced_by: float | None  # min_gap 탈락 시 그 이웃 선택점의 t


class KeyframeReport(TypedDict):
    picks: list[Candidate]
    rejected: list[RejectedCandidate]


class _CandidatePool:
    """Mutable accumulator optimized for timestamp-neighborhood merges."""

    def __init__(self, start: float, end: float) -> None:
        self._start = start
        self._end = end
        self._candidates: dict[float, Candidate] = {}
        self._buckets: dict[int, list[float]] = {}
        self._insertion_order: dict[float, int] = {}

    def add(self, t: float, weight: float, source: str) -> None:
        if not self._start <= t <= self._end:
            return
        bucket = math.floor(t / _MERGE_EPS)
        matches = [
            key
            for nearby in range(bucket - 1, bucket + 2)
            for key in self._buckets.get(nearby, ())
            if abs(key - t) <= _MERGE_EPS
        ]
        if matches:
            key = min(matches, key=self._insertion_order.__getitem__)
            candidate = self._candidates[key]
            candidate["weight"] = max(candidate["weight"], weight)
            if source not in candidate["source"].split("+"):
                candidate["source"] += f"+{source}"
            return
        self._insertion_order[t] = len(self._insertion_order)
        self._buckets.setdefault(bucket, []).append(t)
        self._candidates[t] = {
            "t": min(max(round(t, 2), self._start), self._end),
            "weight": weight,
            "source": source,
        }

    def sorted_values(self) -> list[Candidate]:
        return sorted(self._candidates.values(), key=lambda candidate: candidate["t"])


def _segment_uncertain(seg: TranscriptSegment) -> bool:
    conf = seg.get("conf")
    if isinstance(conf, (int, float)) and not isinstance(conf, bool):
        return conf < 0.6
    logprob = seg.get("logprob")
    no_speech_prob = seg.get("no_speech_prob")
    return (
        isinstance(logprob, (int, float))
        and not isinstance(logprob, bool)
        and logprob < -1.0
    ) or (
        isinstance(no_speech_prob, (int, float))
        and not isinstance(no_speech_prob, bool)
        and no_speech_prob > 0.5
    )


def collect_candidates(
    ws: Workspace,
    *,
    start: float | None = None,
    end: float | None = None,
    legible_endcard: bool = False,
) -> list[Candidate]:
    """신호 JSON을 범위 내 후보로 병합한다; 범위 밖 시점은 먼저 버린다.

    legible_endcard=True면 고정 `duration-1` 대신 꼬리 후보 중 판독
    가능성이 가장 높은 시점을 쓴다(프레임 디코드를 요구하므로 기본은
    끈다 — 신호 JSON만 읽는 결정적·무디코드 경로를 보존).
    """
    duration = float(ws.manifest.get("duration") or 0.0)
    lo = 0.0 if start is None else start
    hi = duration if end is None else end
    pool = _CandidatePool(lo, hi)

    for sc in load_json(ws.root / "scenes.json") or []:
        pool.add(
            sc["t"],
            _W_SCENE_BASE + min(sc.get("score", 0.0), 0.5),
            "scene",
        )
    for hl in load_json(ws.root / "highlights.json") or []:
        pool.add((hl["start"] + hl["end"]) / 2, _W_BURST_MID, "burst-mid")
        pool.add(hl["start"], _W_BURST_EDGE, "burst-edge")
        pool.add(hl["end"], _W_BURST_EDGE, "burst-edge")
    try:
        cps = load_checkpoints(ws)
    except (OSError, ValueError):
        cps = []
    for cp in cps:
        if cp.get("status") == "verified":
            continue
        if float(cp.get("confidence", 0.0)) >= _UNCERTAIN_CONF:
            continue
        span = cp.get("span") or []
        if len(span) == 2:
            weight = 0.5 + (_UNCERTAIN_CONF - float(cp.get("confidence", 0.0)))
            pool.add(
                (span[0] + span[1]) / 2,
                min(weight, 1.0),
                "cp-uncertain",
            )
    for seg in load_transcript_segments(ws.transcript_path):
        if _segment_uncertain(seg):
            pool.add(
                (seg["start"] + seg["end"]) / 2,
                _W_LOWCONF_SEG,
                "asr-lowconf",
            )
    if duration > 1.0 and (end is None or hi >= duration - 1.0):
        endcard_t = duration - 1.0
        source = "endcard-rule"
        if legible_endcard:
            from .endcard import endcard_pick

            # 요청 범위를 상한으로 넘긴다 — 넘겨 고른 시점은 아래 pool.add에서
            # 범위 밖으로 버려져 엔드카드 후보가 통째로 사라진다.
            picked = endcard_pick(ws, limit=hi)
            if picked is not None and lo <= picked <= hi:
                endcard_t, source = picked, "endcard-legible"
        pool.add(endcard_t, _W_ENDCARD, source)

    return pool.sorted_values()


def select_keyframes_report(
    candidates: list[Candidate],
    budget: int,
    duration: float,
    *,
    min_gap: float = 1.0,
    coverage_weight: float = 0.5,
) -> KeyframeReport:
    """그리디 선택 + 무손실 탈락 회계 (skip-with-reason).

    선택 동작은 select_keyframes와 동일하고, 탈락 후보가 왜(min_gap 이웃
    정리 vs 예산 소진) 빠졌는지를 기록한다 — frozen replay의 endcard
    crowding-out처럼 "무엇이 밀려났는가"를 ablation 재실행 없이 산출물에서
    바로 판독하기 위함이다. 입력이 같으면 출력이 같다(동점은 이른 t 우선).
    """
    if budget <= 0 or not candidates:
        return {"picks": [], "rejected": []}
    ideal_gap = max(duration / max(budget, 1), 1e-9)
    remaining = sorted(candidates, key=lambda c: c["t"])
    selected: list[Candidate] = []
    rejected: list[RejectedCandidate] = []
    while remaining and len(selected) < budget:
        best, best_gain = remaining[0], float("-inf")
        for c in remaining:
            if selected:
                dist = min(abs(c["t"] - s["t"]) for s in selected)
                gain = c["weight"] + coverage_weight * min(dist / ideal_gap, 1.0)
            else:
                gain = c["weight"] + coverage_weight
            if gain > best_gain or (gain == best_gain and c["t"] < best["t"]):
                best, best_gain = c, gain
        selected.append(best)
        survivors: list[Candidate] = []
        for c in remaining:
            if c["t"] == best["t"]:
                continue
            if abs(c["t"] - best["t"]) < min_gap:
                rejected.append({**c, "reason": "min_gap_displaced",
                                 "displaced_by": best["t"]})
            else:
                survivors.append(c)
        remaining = survivors
    rejected.extend(
        {**c, "reason": "budget_exhausted", "displaced_by": None}
        for c in remaining
    )
    selected.sort(key=lambda c: c["t"])
    rejected.sort(key=lambda c: c["t"])
    return {"picks": selected, "rejected": rejected}


def select_keyframes(
    candidates: list[Candidate],
    budget: int,
    duration: float,
    *,
    min_gap: float = 1.0,
    coverage_weight: float = 0.5,
) -> list[Candidate]:
    """그리디 선택: 신호 가중 + 커버리지 이득(선택점과의 최소 거리) 최대화.

    커버리지 이득은 duration/budget(이상 간격) 대비 정규화·1.0 상한 —
    한 구간에 고가중 후보가 몰려도 시간축 전체가 커버되게 한다.
    입력이 같으면 출력이 같다(동점은 이른 t 우선).
    """
    return select_keyframes_report(
        candidates, budget, duration,
        min_gap=min_gap, coverage_weight=coverage_weight,
    )["picks"]


def plan_report(
    ws: Workspace,
    budget: int,
    *,
    start: float | None = None,
    end: float | None = None,
    min_gap: float = 1.0,
    legible_endcard: bool = False,
) -> KeyframeReport:
    duration = float(ws.manifest.get("duration") or 0.0)
    cands = collect_candidates(
        ws, start=start, end=end, legible_endcard=legible_endcard
    )
    lo = 0.0 if start is None else start
    hi = duration if end is None else end
    window_duration = max(hi - lo, 0.0)
    return select_keyframes_report(
        cands, budget, window_duration, min_gap=min_gap
    )


def plan(
    ws: Workspace,
    budget: int,
    *,
    start: float | None = None,
    end: float | None = None,
    min_gap: float = 1.0,
    legible_endcard: bool = False,
) -> list[Candidate]:
    return plan_report(
        ws, budget, start=start, end=end, min_gap=min_gap,
        legible_endcard=legible_endcard,
    )["picks"]
