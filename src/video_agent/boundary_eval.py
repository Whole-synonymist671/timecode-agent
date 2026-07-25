"""컷 경계의 결정적 시청각 평가 — 쿨레쇼프 루프의 기계 게이트 절반.

쿨레쇼프 루프(Kuleshov loop): 편집 결정 검증의 왕복 — Pareto 다안 →
단어 경계 스냅 → 본 모듈(결정적 오디오·단어 지표) → 비전 self-eval →
재스냅 → 터미널 승격(리비전 고정). 컷의 품질은 단일 프레임이 아니라
경계 전후의 병치 관계에서 판정된다는 원칙(쿨레쇼프 효과)의 이름이다.
두 게이트는 상호보완이 실측됐다: 오디오 지표는 비전이 못 보는 단어
절단을(거제 903.9), 비전은 오디오가 못 보는 화면 전환 침범을(더블비
279.8 엔드카드) 잡았다. 여기서는 ffmpeg astats·words.json만으로 결정적
수치와 플래그를 내며, 채택·거부 판정은 에이전트 몫이다(자문 지표).
"""

from __future__ import annotations

import re
from typing import TypedDict

from .proc import run
from .workspace import Workspace, load_json

_RMS_RE = re.compile(r"RMS level dB:\s*(-?[0-9.]+|-inf)")
_RMS_FLOOR_DB = -99.0  # -inf(순수 무음)를 JSON 직렬화 가능한 바닥값으로


class WordAlignment(TypedDict):
    in_word: str | None
    prev_word_end: float | None
    next_word_start: float | None
    gap: float | None
    tail_gap: float | None


class BoundaryReport(TypedDict):
    t: float
    rms_before_db: float | None
    rms_after_db: float | None
    rms_step_db: float | None
    alignment: WordAlignment | None
    flags: list[str]


class JoinReport(TypedDict):
    """렌더 결과에서 병치되는 조인(컷 종료↔다음 컷 시작)의 급차."""

    from_t: float
    to_t: float
    rms_out_db: float | None
    rms_in_db: float | None
    rms_step_db: float | None
    ssim: float | None
    flags: list[str]


class SequenceGateReport(TypedDict):
    boundaries: list[BoundaryReport]
    joins: list[JoinReport]


def word_alignment(ws: Workspace, t: float) -> WordAlignment | None:
    """words.json 기준 컷 위치의 단어 정렬 — 파일이 없으면 None."""
    words = load_json(ws.root / "words.json") or []
    if not words:
        return None
    in_word = next(
        (
            str(word["word"]).strip()
            for word in words
            if float(word["start"]) < t < float(word["end"])
        ),
        None,
    )
    prev_end = max(
        (float(w["end"]) for w in words if float(w["end"]) <= t), default=None
    )
    next_start = min(
        (float(w["start"]) for w in words if float(w["start"]) >= t),
        default=None,
    )
    inside = in_word is not None
    gap = (
        round(next_start - prev_end, 6)
        if not inside and prev_end is not None and next_start is not None
        else None
    )
    return {
        "in_word": in_word,
        "prev_word_end": prev_end,
        "next_word_start": next_start,
        "gap": gap,
        "tail_gap": (
            round(t - prev_end, 6)
            if not inside and prev_end is not None else None
        ),
    }


def rms_db(ws: Workspace, start: float, duration: float) -> float | None:
    """구간 오디오의 Overall RMS(dB) — ffmpeg astats, 결정적."""
    if duration <= 0:
        return None
    res = run([
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-ss", f"{max(start, 0.0):.3f}", "-t", f"{duration:.3f}",
        "-i", str(ws.video), "-vn",
        "-af", "astats=measure_perchannel=none:measure_overall=RMS_level",
        "-f", "null", "-",
    ], capture_output=True, text=True)
    matches = _RMS_RE.findall(res.stderr or "")
    if res.returncode != 0 or not matches:
        return None
    value = matches[-1]
    return _RMS_FLOOR_DB if value == "-inf" else max(
        float(value), _RMS_FLOOR_DB
    )


def cut_boundary_report(
    ws: Workspace,
    t: float,
    *,
    window: float = 0.4,
    tail_min: float = 0.12,
    step_db: float = 12.0,
) -> BoundaryReport:
    """한 컷 지점의 자문 리포트 — 플래그 기본값은 문서화된 휴리스틱.

    - word_interior: 단어 내부 절단 (스냅 실패 — 가장 심각)
    - tight_tail: 간극이 충분한데 직전 단어에 밀착해 호흡을 잘라냄
    - loud_step: 경계 전후 RMS 급차 (룸톤·음악 불연속 의심)
    """
    duration = float(ws.manifest.get("duration") or 0.0)
    before = rms_db(ws, t - window, min(window, max(t, 0.0)))
    after = rms_db(ws, t, min(window, max(duration - t, 0.0)))
    step = (
        round(abs(before - after), 2)
        if before is not None and after is not None else None
    )
    alignment = word_alignment(ws, t)
    flags: list[str] = []
    if alignment and alignment["in_word"]:
        flags.append("word_interior")
    if (
        alignment
        and alignment["tail_gap"] is not None
        and alignment["gap"] is not None
        and alignment["tail_gap"] < tail_min
        and alignment["gap"] >= 2.5 * tail_min
    ):
        flags.append("tight_tail")
    if step is not None and step > step_db:
        flags.append("loud_step")
    return {
        "t": t,
        "rms_before_db": before,
        "rms_after_db": after,
        "rms_step_db": step,
        "alignment": alignment,
        "flags": sorted(flags),
    }


def _sequence_cuts(ws: Workspace, sequence_id: str) -> list[dict]:
    from .sequence import load_sequences

    sequences = {seq["id"]: seq for seq in load_sequences(ws)}
    if sequence_id not in sequences:
        raise ValueError(
            f"unknown sequence id: {sequence_id}"
            f" (있는 것: {sorted(sequences)})"
        )
    return sequences[sequence_id]["cuts"]


def sequence_boundary_reports(
    ws: Workspace, sequence_id: str, *, window: float = 0.4
) -> list[BoundaryReport]:
    """편집 원장의 한 시퀀스가 가진 전 컷 경계를 한 번에 게이트한다.

    쿨레쇼프 루프의 기계 게이트를 시퀀스 단위로 — 경계 타임스탬프를
    수동으로 옮겨 적는 반복(오기 위험)을 제거한다. 공유 경계는 1회만.
    """
    boundaries = sorted({
        round(float(edge), 3)
        for cut in _sequence_cuts(ws, sequence_id)
        for edge in cut["span"]
    })
    return [
        cut_boundary_report(ws, t, window=window) for t in boundaries
    ]


_SSIM_RE = re.compile(r"All:([0-9.]+)")
_YSTAT_RE = re.compile(r"lavfi\.signalstats\.(YMIN|YMAX)=([0-9.]+)")


def frame_ssim(ws: Workspace, t1: float, t2: float) -> float | None:
    """두 시점 프레임의 SSIM(0~1) — ffmpeg, 결정적. 캡처는 캐시 재사용.

    조인의 시각 병치 판정용: 높으면 같은 구도(시간만 점프하는 점프컷
    위험), 낮으면 뚜렷한 장면 전환이다.
    """
    from .capture import capture_frames

    left, right = (
        capture_frames(ws, [t1], reason="join-gate")[0],
        capture_frames(ws, [t2], reason="join-gate")[0],
    )
    res = run([
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-i", str(left), "-i", str(right),
        "-lavfi", "ssim", "-f", "null", "-",
    ], capture_output=True, text=True)
    match = _SSIM_RE.search(res.stderr or "")
    if res.returncode != 0 or not match:
        return None
    return round(float(match.group(1)), 4)


def frame_luma_range(ws: Workspace, t: float) -> float | None:
    """프레임 휘도 범위(YMAX-YMIN) — 화면에 무언가 그려져 있는지의 하한 신호.

    검은 화면·단색 페이드는 0이고, 글자가 있는 카드는 대비 때문에 한참
    위로 뜬다(실측: 검정 0, 컬러바 219). "선명한가"를 재려던 에지 에너지와
    달리(노이즈가 평면 카드를 이겨 픽이 뒤집혔다) "비어 있는가"만 묻기
    때문에 노이즈에 속지 않는다 — 노이즈도 높게 나오지만, 이 신호는 후보를
    **고르는** 데가 아니라 빈 화면을 **배제하는** 데만 쓴다.
    """
    from .capture import capture_frames

    frame = capture_frames(ws, [t], reason="endcard-gate")[0]
    res = run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(frame),
        # file=- 로 stdout에 찍는다: 로그 레벨에 묻히지 않고, 출력 먹서는
        # /dev/null 로 보내 둘이 같은 파이프를 다투지 않게 한다.
        "-vf", "signalstats,metadata=print:file=-",
        "-f", "null", "/dev/null",
    ], capture_output=True, text=True)
    if res.returncode != 0:
        return None
    stats = dict(_YSTAT_RE.findall(res.stdout or ""))
    if "YMIN" not in stats or "YMAX" not in stats:
        return None
    return round(float(stats["YMAX"]) - float(stats["YMIN"]), 4)


def sequence_join_reports(
    ws: Workspace,
    sequence_id: str,
    *,
    window: float = 0.4,
    step_db: float = 12.0,
    jump_ssim: float = 0.75,
) -> list[JoinReport]:
    """렌더 순서상 인접하게 되는 조인의 급차를 잰다 — 병치가 정본.

    소스 타임라인의 경계 전후(cut_boundary_report)는 원본 연속성을 보고,
    여기는 조립 결과에서 실제로 붙는 두 구간(컷 i 종료 직전 ↔ 컷 i+1
    시작 직후)을 비교한다 — 각 소스 에지가 clean이어도 조인은 큰
    음량 급차(loud_join)이거나 같은 구도의 시간 점프(jump_cut_risk,
    SSIM 기본 0.75 초과)일 수 있다. 조인 프레임은 캡처 캐시에 남아
    이어지는 비전 self-eval에 그대로 재사용된다.
    """
    cuts = _sequence_cuts(ws, sequence_id)
    frame_step = 1.0 / float(ws.manifest.get("fps") or 25.0)
    joins: list[JoinReport] = []
    for left, right in zip(cuts, cuts[1:]):
        from_t = round(float(left["span"][1]), 3)
        to_t = round(float(right["span"][0]), 3)
        out_db = rms_db(ws, from_t - window, min(window, max(from_t, 0.0)))
        in_db = rms_db(ws, to_t, window)
        step = (
            round(abs(out_db - in_db), 2)
            if out_db is not None and in_db is not None else None
        )
        # 좌측 시각 샘플은 렌더 스팬 내부의 끝자락 — 컷 종료점 정확히는
        # 첫 '제외' 프레임(다음 숏·duration 밖)일 수 있고, 1프레임
        # 마진은 seek 정밀도에 밀착해 경계 건너 프레임이 뽑힌다(실측
        # 0.92 오발화) — 2프레임(최소 0.08s) 물러선다.
        left_sample_t = max(
            round(from_t - max(2 * frame_step, 0.08), 3),
            float(left["span"][0]),
        )
        ssim = frame_ssim(ws, left_sample_t, to_t)
        flags: list[str] = []
        if step is not None and step > step_db:
            flags.append("loud_join")
        # 점프컷은 양의 소스 시간 제거가 전제 — 연속 분할(gap≈0)이나
        # 재배열(음수)은 인접 프레임 고SSIM이 정상이므로 억제한다.
        source_gap = to_t - from_t
        if (
            ssim is not None
            and ssim > jump_ssim
            and source_gap > frame_step
        ):
            flags.append("jump_cut_risk")
        joins.append({
            "from_t": from_t,
            "to_t": to_t,
            "rms_out_db": out_db,
            "rms_in_db": in_db,
            "rms_step_db": step,
            "ssim": ssim,
            "flags": sorted(flags),
        })
    return joins


def sequence_gate_report(
    ws: Workspace, sequence_id: str, *, window: float = 0.4
) -> SequenceGateReport:
    return {
        "boundaries": sequence_boundary_reports(
            ws, sequence_id, window=window
        ),
        "joins": sequence_join_reports(ws, sequence_id, window=window),
    }
