"""Canonical half-open stream PTS spans with seconds as a projection."""

from __future__ import annotations

import math
from collections.abc import Mapping
from fractions import Fraction
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from .workspace import Workspace


class CanonicalTemporalSpan(TypedDict):
    stream_id: str
    start_pts: int
    end_pts_exclusive: int
    time_base_num: int
    time_base_den: int
    timing_revision_id: str


class TemporalSpanError(ValueError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)

    def __str__(self) -> str:
        return self.detail


def _timing_contract(ws: Workspace) -> tuple[str, int, Fraction, str] | None:
    manifest = ws.manifest
    revision = manifest.get("timing_revision_id")
    if not isinstance(revision, str):
        return None
    timing = manifest.get("timing")
    if not isinstance(timing, dict):
        raise TemporalSpanError("manifest timing metadata is missing")
    stream_id = timing.get("stream_id")
    time_base_value = timing.get("time_base")
    start_pts = timing.get("start_pts")
    if (
        not isinstance(stream_id, str)
        or not stream_id
        or not isinstance(time_base_value, str)
        or isinstance(start_pts, bool)
        or not isinstance(start_pts, int)
    ):
        raise TemporalSpanError("manifest canonical stream timing is incomplete")
    try:
        time_base = Fraction(time_base_value)
    except (ValueError, ZeroDivisionError) as error:
        raise TemporalSpanError("manifest time_base is invalid") from error
    if time_base <= 0:
        raise TemporalSpanError("manifest time_base must be positive")
    return stream_id, start_pts, time_base, revision


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TemporalSpanError(f"{field} must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise TemporalSpanError(f"{field} must be a finite number") from error
    if not math.isfinite(number):
        raise TemporalSpanError(f"{field} must be a finite number")
    return number


def _parse_temporal_span(
    raw: Mapping[str, object],
) -> CanonicalTemporalSpan:
    stream_id = raw.get("stream_id")
    start_pts = raw.get("start_pts")
    end_pts = raw.get("end_pts_exclusive")
    numerator = raw.get("time_base_num")
    denominator = raw.get("time_base_den")
    revision = raw.get("timing_revision_id")
    if not isinstance(stream_id, str) or not stream_id:
        raise TemporalSpanError("temporal_span.stream_id is invalid")
    integers = (start_pts, end_pts, numerator, denominator)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in integers):
        raise TemporalSpanError("temporal_span PTS and time base must be integers")
    assert isinstance(start_pts, int)
    assert isinstance(end_pts, int)
    assert isinstance(numerator, int)
    assert isinstance(denominator, int)
    if start_pts >= end_pts or numerator <= 0 or denominator <= 0:
        raise TemporalSpanError("temporal_span must be positive and non-empty")
    if not isinstance(revision, str) or not revision:
        raise TemporalSpanError("temporal_span.timing_revision_id is invalid")
    return {
        "stream_id": stream_id,
        "start_pts": start_pts,
        "end_pts_exclusive": end_pts,
        "time_base_num": numerator,
        "time_base_den": denominator,
        "timing_revision_id": revision,
    }


def canonicalize_span(
    ws: Workspace,
    span: object,
    temporal_span: object = None,
) -> tuple[list[float], CanonicalTemporalSpan | None]:
    """Snap seconds input to PTS and validate a supplied canonical span."""
    contract = _timing_contract(ws)
    if contract is None:
        if temporal_span is not None:
            raise TemporalSpanError(
                "legacy workspace cannot validate canonical temporal_span"
            )
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            raise TemporalSpanError("span must contain start and end seconds")
        return [_number(span[0], "span start"), _number(span[1], "span end")], None

    stream_id, origin_pts, time_base, timing_revision = contract
    duration = _number(ws.manifest.get("duration"), "manifest duration")
    duration_fraction = Fraction(str(duration))
    if duration_fraction <= 0:
        raise TemporalSpanError("manifest duration must be positive")
    media_end_pts = origin_pts + math.ceil(duration_fraction / time_base)
    if temporal_span is not None:
        if not isinstance(temporal_span, dict):
            raise TemporalSpanError("temporal_span must be an object")
        canonical = _parse_temporal_span(temporal_span)
        if (
            canonical["stream_id"] != stream_id
            or canonical["time_base_num"] != time_base.numerator
            or canonical["time_base_den"] != time_base.denominator
            or canonical["timing_revision_id"] != timing_revision
        ):
            raise TemporalSpanError("temporal_span timing revision mismatch")
        if canonical["end_pts_exclusive"] > media_end_pts:
            raise TemporalSpanError("temporal_span exceeds media duration")
    else:
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            raise TemporalSpanError("span must contain start and end seconds")
        start = _number(span[0], "span start")
        end = _number(span[1], "span end")
        if not 0 <= start < end:
            raise TemporalSpanError("span must satisfy 0 <= start < end")
        if end > duration:
            raise TemporalSpanError("span end must not exceed media duration")
        canonical: CanonicalTemporalSpan = {
            "stream_id": stream_id,
            "start_pts": origin_pts + math.floor(Fraction(str(start)) / time_base),
            "end_pts_exclusive": origin_pts
            + math.ceil(Fraction(str(end)) / time_base),
            "time_base_num": time_base.numerator,
            "time_base_den": time_base.denominator,
            "timing_revision_id": timing_revision,
        }

    canonical_base = Fraction(
        canonical["time_base_num"], canonical["time_base_den"]
    )
    projected_start = (
        canonical["start_pts"] - origin_pts
    ) * canonical_base
    projected_end = min(
        duration_fraction,
        (canonical["end_pts_exclusive"] - origin_pts) * canonical_base,
    )
    if not 0 <= projected_start < projected_end:
        raise TemporalSpanError("temporal_span projects outside the media timeline")
    return [float(projected_start), float(projected_end)], canonical
