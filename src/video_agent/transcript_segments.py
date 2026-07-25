"""Fail-closed transcript segment loading for derived surfaces."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import NotRequired, TypedDict, TypeGuard, cast

from .workspace import load_json

type TranscriptValue = (
    str
    | int
    | float
    | bool
    | None
    | list[TranscriptValue]
    | dict[str, TranscriptValue]
)


class TranscriptSegment(TypedDict):
    """Validated required fields; parsed records may retain additional fields."""

    start: int | float
    end: int | float
    text: str
    conf: NotRequired[TranscriptValue]


def _is_finite_number(value: TranscriptValue) -> TypeGuard[int | float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def normalize_transcript_segments(
    value: TranscriptValue,
) -> list[TranscriptSegment]:
    """Keep valid records from a transcript array without rewriting them."""
    if not isinstance(value, list):
        return []

    segments: list[TranscriptSegment] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        text = item.get("text")
        if (
            not _is_finite_number(start)
            or not _is_finite_number(end)
            or not isinstance(text, str)
            or not 0 <= start < end
        ):
            continue
        # Validation above establishes the required TypedDict keys and types.
        # Keep the parsed mapping itself so optional producer-specific fields
        # survive normalization unchanged.
        segments.append(cast(TranscriptSegment, item))
    return segments


def load_transcript_segments(path: Path) -> list[TranscriptSegment]:
    """Return normalized segments, or an empty list for unavailable JSON."""
    value: TranscriptValue = load_json(path)
    return normalize_transcript_segments(value)


def load_transcript_segments_for_update(path: Path) -> list[TranscriptSegment]:
    """Load a writable transcript without collapsing corruption into empty.

    Read-only projections intentionally degrade missing or partial JSON to an
    empty view. A mutating consumer must instead distinguish a valid empty
    transcript from unavailable/corrupt source data before replacing it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise OSError(f"transcript is unavailable for update: {path}") from error
    try:
        value = cast(TranscriptValue, json.loads(text))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid transcript JSON: {path}") from error
    if not isinstance(value, list):
        raise ValueError(f"transcript must be a top-level array: {path}")
    segments = normalize_transcript_segments(value)
    if value and not segments:
        raise ValueError(f"transcript contains no valid segments: {path}")
    return segments
