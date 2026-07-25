"""Schema and state-transition validation for edit-decision sequences."""

from __future__ import annotations

import math
import re
from typing import TypeGuard

from .workspace import Workspace

_ID_RE = re.compile(r"^seq-\d{3,}$")
SEQUENCE_SCHEMA_VERSION = 2
SEQUENCE_STATUSES = ("assembled", "boundary_verified", "exported")
SEQ_STATUS_KO = {
    "assembled": "조립됨",
    "boundary_verified": "경계 확인",
    "exported": "내보냄",
}

SEQUENCE_SCHEMA_EXAMPLE = """\
예시:
{"id": "seq-001", "intent": "예능 하이라이트 쇼츠",
 "brief": {"platform": "premiere", "aspect": "9:16", "target_len_s": 60},
 "cuts": [{"span": [95.0, 118.4], "order": 1, "role": "hook",
           "checkpoint_ids": ["cp-004"], "signals": ["burst-97s"],
           "note": "환호 직후 폭소 구간"}],
 "alternatives_rejected": [{"span": [200.0, 210.0], "reason": "페이오프 없음"}],
 "expected_effect": "긴장 상승 후 폭소 페이오프",
 "status": "assembled"}"""


class SequenceValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SequenceValidationError(message)


def _num(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _str_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


def _xml_1_0_char_allowed(char: str) -> bool:
    codepoint = ord(char)
    return (
        codepoint in (0x09, 0x0A, 0x0D)
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def _grounding_ref_list(value: object) -> bool:
    return _str_list(value) and all(
        item == " ".join(item.splitlines())
        and all(_xml_1_0_char_allowed(char) for char in item)
        for item in value
    )


def validate_sequence_object(
    ws: Workspace,
    obj: object,
    *,
    prior: dict | None,
    known_checkpoints: set[str],
    legacy_compat: bool = False,
) -> dict:
    """Validate the sequence-local schema without interpreting evidence."""
    if not isinstance(obj, dict):
        raise SequenceValidationError("sequence는 JSON 객체여야 합니다")
    seq: dict = dict(obj)
    schema_version = seq.get("schema_version")
    if legacy_compat:
        _require(
            schema_version is None,
            "legacy sequence에는 schema_version이 없어야 합니다",
        )
    else:
        _require(
            schema_version in (None, SEQUENCE_SCHEMA_VERSION),
            f"schema_version은 {SEQUENCE_SCHEMA_VERSION}이어야 합니다",
        )
        seq["schema_version"] = SEQUENCE_SCHEMA_VERSION

    seq_id = seq.get("id")
    if not (isinstance(seq_id, str) and _ID_RE.fullmatch(seq_id)):
        raise SequenceValidationError(
            "id는 'seq-001' 형식(seq-숫자 3자리+)이어야 합니다"
        )
    intent = seq.get("intent")
    if not (isinstance(intent, str) and intent.strip()):
        raise SequenceValidationError("intent(편집 의도)는 비울 수 없습니다")

    status = (
        seq["status"]
        if "status" in seq
        else prior["status"]
        if prior
        else "assembled"
    )
    _require(
        status in SEQUENCE_STATUSES,
        f"status는 {SEQUENCE_STATUSES} 중 하나여야 합니다",
    )
    if prior and not legacy_compat:
        _require(
            SEQUENCE_STATUSES.index(status)
            >= SEQUENCE_STATUSES.index(prior["status"]),
            f"status 후퇴는 허용되지 않습니다: "
            f"{prior['status']} -> {status}",
        )
    seq["status"] = status

    effect = seq.get("expected_effect")
    _require(
        effect is None or isinstance(effect, str),
        "expected_effect는 문자열이어야 합니다",
    )
    _require(
        seq.get("brief") is None or isinstance(seq["brief"], dict),
        "brief는 객체여야 합니다",
    )

    duration = float(ws.manifest["duration"])
    cuts = seq.get("cuts")
    if not (isinstance(cuts, list) and cuts):
        raise SequenceValidationError("cuts는 비어 있지 않은 배열이어야 합니다")
    orders: list[int] = []
    for index, cut in enumerate(cuts):
        where = f"cuts[{index}]"
        if not isinstance(cut, dict):
            raise SequenceValidationError(f"{where}는 객체여야 합니다")
        span = cut.get("span")
        if not (
            isinstance(span, list)
            and len(span) == 2
            and all(_num(value) for value in span)
        ):
            raise SequenceValidationError(
                f"{where}.span은 [시작초, 끝초]여야 합니다"
            )
        start, end = float(span[0]), float(span[1])
        upper_bound = duration + 0.5 if legacy_compat else duration
        _require(
            0 <= start < end <= upper_bound,
            f"{where}.span이 영상 범위(0~{duration:.2f}s)를 벗어납니다",
        )

        order = cut.get("order", index + 1)
        if not (
            isinstance(order, int)
            and not isinstance(order, bool)
            and order >= 1
        ):
            raise SequenceValidationError(
                f"{where}.order는 1 이상의 정수여야 합니다"
            )
        cut["order"] = order
        orders.append(order)
        for key in ("note", "role"):
            value = cut.get(key)
            _require(
                value is None or isinstance(value, str),
                f"{where}.{key}는 문자열이어야 합니다",
            )

        checkpoint_ids = cut.get("checkpoint_ids", [])
        signals = cut.get("signals", [])
        if not _grounding_ref_list(checkpoint_ids):
            raise SequenceValidationError(
                f"{where}.checkpoint_ids는 문자열 배열이어야 합니다"
            )
        if not _grounding_ref_list(signals):
            raise SequenceValidationError(
                f"{where}.signals는 문자열 배열이어야 합니다"
            )
        _require(
            bool(checkpoint_ids) or bool(signals),
            f"{where}: 근거 없는 컷 금지 — checkpoint_ids 또는 "
            "signals 중 하나는 필수",
        )
        unknown = [
            checkpoint_id
            for checkpoint_id in checkpoint_ids
            if checkpoint_id not in known_checkpoints
        ]
        _require(
            not unknown,
            f"{where}.checkpoint_ids에 미존재 체크포인트: {unknown}",
        )

    _require(len(set(orders)) == len(orders), "cuts[].order가 중복됩니다")
    seq["cuts"] = sorted(cuts, key=lambda cut: cut["order"])

    rejected = seq.get("alternatives_rejected")
    if rejected is not None:
        if not isinstance(rejected, list):
            raise SequenceValidationError(
                "alternatives_rejected는 객체 배열이어야 합니다"
            )
        for index, alternative in enumerate(rejected):
            where = f"alternatives_rejected[{index}]"
            if not isinstance(alternative, dict):
                raise SequenceValidationError(f"{where}는 객체여야 합니다")
            span = alternative.get("span")
            if not (
                isinstance(span, list)
                and len(span) == 2
                and all(_num(value) for value in span)
            ):
                raise SequenceValidationError(
                    f"{where}.span은 [시작초, 끝초]여야 합니다"
                )
            start, end = float(span[0]), float(span[1])
            _require(
                0 <= start < end <= duration,
                f"{where}.span이 영상 범위(0~{duration:.2f}s)를 벗어납니다",
            )
            reason = alternative.get("reason")
            _require(
                isinstance(reason, str) and bool(reason.strip()),
                f"{where}.reason은 비어 있지 않은 문자열이어야 합니다",
            )

    overrides = seq.get("human_overrides")
    if overrides is not None and not _str_list(overrides):
        raise SequenceValidationError(
            "human_overrides는 비어 있지 않은 문자열 배열이어야 합니다"
        )

    revisions = seq.get("checkpoint_revisions")
    if revisions is not None and not (
        isinstance(revisions, dict)
        and all(
            isinstance(key, str)
            and bool(key.strip())
            and isinstance(value, str)
            and bool(value.strip())
            for key, value in revisions.items()
        )
    ):
        raise SequenceValidationError(
            "checkpoint_revisions는 {체크포인트 id: 리비전 해시} 객체여야 "
            "합니다"
        )
    return seq
