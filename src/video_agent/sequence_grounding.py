"""Atomic checkpoint snapshot and terminal grounding policy."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from . import checkpoint_store
from .sequence_schema import SequenceValidationError
from .verification import _verification_audit_from_entries
from .workspace import Workspace

type GroundingSnapshot = tuple[dict[str, dict], set[str]]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SequenceValidationError(message)


def _spans_overlap(left: list[float], right: list[float]) -> bool:
    return max(left[0], right[0]) < min(left[1], right[1])


# 컷 경계 여백 — 컷 경계는 단어 경계로 정밀 스냅되지만 체크포인트 span
# 경계는 사람이 대략 잡는다(실측 2026-07-26: terminal 컷 6개 중 5개는 완전
# 커버, 1개가 근거 시작보다 1.10s 앞섬 — 원장 note에 "단어 경계 직전
# 간극"으로 남은 의도적 여백). 그 대략성만 흡수하고 컷 몸통의 미커버는
# 근거 없는 편집이라 막는다. 짧은 컷이 여백만으로 채워지지 않게 비율 상한도
# 함께 둔다(5s 컷의 허용 여백은 0.5s).
_EDGE_SLACK_MAX = 2.0
_EDGE_SLACK_RATIO = 0.1
_SPAN_EPSILON = 1e-9


def uncovered_spans(
    cut_span: tuple[float, float] | list[float],
    checkpoint_spans: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """컷 span에서 체크포인트 span 합집합이 덮지 못한 구간들 (시간순)."""
    remaining = [(float(cut_span[0]), float(cut_span[1]))]
    for start, end in checkpoint_spans:
        carved: list[tuple[float, float]] = []
        for gap_start, gap_end in remaining:
            if end <= gap_start or start >= gap_end:
                carved.append((gap_start, gap_end))
                continue
            if start > gap_start:
                carved.append((gap_start, min(start, gap_end)))
            if end < gap_end:
                carved.append((max(end, gap_start), gap_end))
        remaining = carved
    return sorted(
        (start, end)
        for start, end in remaining
        if end - start > _SPAN_EPSILON
    )


def _require_cut_coverage(
    where: str,
    cut: Mapping[str, object],
    checkpoint_spans: list[tuple[float, float]],
) -> None:
    """컷 몸통 전체가 terminal 근거로 덮이는지 — 겹침은 커버리지가 아니다.

    겹침만 요구하면 99–100s 체크포인트 하나가 0–100s 컷을 승격시킨다.
    """
    span = cut["span"]
    assert isinstance(span, list)
    start, end = float(span[0]), float(span[1])
    slack = min(_EDGE_SLACK_MAX, (end - start) * _EDGE_SLACK_RATIO)
    for gap_start, gap_end in uncovered_spans((start, end), checkpoint_spans):
        length = gap_end - gap_start
        at_edge = (
            gap_start - start <= _SPAN_EPSILON
            or end - gap_end <= _SPAN_EPSILON
        )
        if at_edge and length <= slack + _SPAN_EPSILON:
            continue
        _require(
            False,
            f"{where}: 컷 {start:.2f}–{end:.2f}s 중 "
            f"{gap_start:.2f}–{gap_end:.2f}s({length:.2f}s)에 terminal 근거가 "
            "없습니다 — 겹침만으로는 승격할 수 없습니다. 그 구간을 덮는 "
            "체크포인트를 검증해 추가하거나 컷 경계를 좁히십시오"
            + (
                f" (컷 경계 여백은 {slack:.2f}s까지 허용)"
                if at_edge
                else " (컷 몸통의 미커버는 여백으로 허용되지 않습니다)"
            ),
        )


@contextmanager
def checkpoint_grounding_snapshot(
    ws: Workspace,
) -> Iterator[GroundingSnapshot]:
    """Hold one checkpoint revision set through sequence validation/write."""
    with checkpoint_store._checkpoint_lock(ws, exclusive=False):
        entries = checkpoint_store._load_checkpoint_entries_unlocked(ws)
        checkpoints_by_id = {
            validated.checkpoint_id: checkpoint
            for validated, checkpoint in entries
        }
        unsupported_ids = {
            issue["checkpoint_id"]
            for issue in _verification_audit_from_entries(ws, entries)["issues"]
            if issue["code"] == "missing_support"
        }
        yield checkpoints_by_id, unsupported_ids


def checkpoint_revision_hash(checkpoint: Mapping[str, object]) -> str:
    """정규형 JSON(sha256)으로 체크포인트 내용 리비전을 식별한다."""
    canonical = json.dumps(
        checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _referenced_checkpoint_ids(seq: dict) -> list[str]:
    seen: dict[str, None] = {}
    for cut in seq["cuts"]:
        for checkpoint_id in cut.get("checkpoint_ids") or []:
            seen.setdefault(checkpoint_id, None)
    return list(seen)


def revision_drift(
    seq: dict, checkpoints_by_id: Mapping[str, Mapping[str, object]]
) -> list[str]:
    """고정 리비전과 현재 체크포인트 내용이 어긋난 id 목록 (읽기 전용)."""
    pinned = seq.get("checkpoint_revisions")
    if not isinstance(pinned, dict):
        return []
    return sorted(
        checkpoint_id
        for checkpoint_id, revision in pinned.items()
        if (checkpoint := checkpoints_by_id.get(checkpoint_id)) is None
        or checkpoint_revision_hash(checkpoint) != revision
    )


def _require_current(pinned: dict, current: dict[str, str]) -> None:
    drifted = sorted(
        checkpoint_id
        for checkpoint_id, revision in pinned.items()
        if current.get(checkpoint_id) != revision
    )
    _require(
        not drifted,
        f"checkpoint_revisions 불일치: {drifted} — 고정 시점 이후 "
        "체크포인트가 갱신되었습니다. 프레임을 재검증한 뒤 현재 리비전 "
        "해시를 checkpoint_revisions에 명시해야 재승격할 수 있습니다",
    )


def pin_terminal_revisions(
    seq: dict,
    checkpoints_by_id: dict[str, dict],
    *,
    prior: dict | None,
) -> None:
    """쓰기 시점에 승격 근거의 내용 리비전을 검증하고 고정한다.

    원장 replay에는 적용하지 않는다 — 과거 라인은 쓰인 그대로 실려 오고,
    소급 고정(당시 내용을 모르면서 현재 해시를 심는 위조)도 하지 않는다.
    드리프트의 유일한 흡수 경로는 현재 해시의 명시적 재고정이다.
    """
    referenced = _referenced_checkpoint_ids(seq)
    current = {
        checkpoint_id: checkpoint_revision_hash(
            checkpoints_by_id[checkpoint_id]
        )
        for checkpoint_id in referenced
    }
    asserted = seq.get("checkpoint_revisions")
    if asserted is not None:
        # 부분·빈 단언은 prior 대조를 건너뛰며 누락분이 현재 해시로 조용히
        # 채워지는 드리프트 무감사 흡수 경로가 된다 — 정확 커버만 허용.
        missing = sorted(set(current) - set(asserted))
        extras = sorted(set(asserted) - set(current))
        _require(
            not missing and not extras,
            "checkpoint_revisions는 참조 체크포인트 전체를 정확히 덮어야 "
            f"합니다 — 누락: {missing}, 참조되지 않음: {extras}",
        )
        _require_current(asserted, current)
    if seq["status"] == "assembled":
        return
    if asserted is None and prior:
        prior_pins = prior.get("checkpoint_revisions")
        if isinstance(prior_pins, dict):
            _require_current(
                {
                    checkpoint_id: revision
                    for checkpoint_id, revision in prior_pins.items()
                    if checkpoint_id in current
                },
                current,
            )
    seq["checkpoint_revisions"] = current


def validate_terminal_grounding(
    seq: dict,
    checkpoints_by_id: dict[str, dict],
    unsupported_ids: set[str],
) -> None:
    """Require every terminal reference to be supported and time-aligned."""
    if seq["status"] == "assembled":
        return

    for index, cut in enumerate(seq["cuts"]):
        where = f"cuts[{index}]"
        checkpoint_ids = cut.get("checkpoint_ids") or []
        _require(
            bool(checkpoint_ids),
            f"{where}: {seq['status']} 승격에는 checkpoint_ids가 필수입니다 "
            "— canonical signal registry가 없어 signals-only 승격은 "
            "허용되지 않습니다",
        )
        grounded_spans: list[tuple[float, float]] = []
        for checkpoint_id in checkpoint_ids:
            checkpoint = checkpoints_by_id[checkpoint_id]
            _require(
                checkpoint.get("status") in ("verified", "corrected"),
                f"{where}.checkpoint_ids의 {checkpoint_id}는 "
                "verified/corrected 상태여야 합니다",
            )
            _require(
                checkpoint_id not in unsupported_ids,
                f"{where}.checkpoint_ids의 {checkpoint_id}에 실제 검증 근거가 "
                "없습니다",
            )
            checkpoint_span = checkpoint.get("span")
            _require(
                isinstance(checkpoint_span, list)
                and len(checkpoint_span) == 2
                and _spans_overlap(cut["span"], checkpoint_span),
                f"{where}.span과 checkpoint {checkpoint_id}가 시간상 "
                "겹치지 않습니다",
            )
            assert isinstance(checkpoint_span, list)
            grounded_spans.append(
                (float(checkpoint_span[0]), float(checkpoint_span[1]))
            )
        _require_cut_coverage(where, cut, grounded_spans)
