"""Typed contracts for image artifacts and causal provenance edges."""

from __future__ import annotations

from typing import NotRequired, TypedDict

from .temporal import CanonicalTemporalSpan


class ImageMetadata(TypedDict, total=False):
    t: float
    span: list[float]
    timestamps: list[float]
    crop: str | None
    requested_t: float
    window: float
    role: str
    scores: dict[str, float]
    cells: int
    cols: int
    render_version: int
    metadata: dict[str, object]
    temporal_span: CanonicalTemporalSpan
    source_revision_id: str
    transcript_revision_id: str
    timing_revision_id: str


class ImageEventInput(ImageMetadata, total=False):
    path: str
    file: str
    kind: str
    cause_type: str
    cause_id: str
    reason: str | None


class _RequiredImageEvent(TypedDict):
    schema: int
    path: str
    file: str
    kind: str
    image_id: str
    cause_id: str
    cause_type: str
    edge_id: str
    reason: str | None
    source: str


class ImageEvent(_RequiredImageEvent, ImageMetadata):
    pass


class ImageCause(TypedDict):
    edge_id: str
    cause_id: str
    cause_type: str
    reason: str | None
    source: str
    role: NotRequired[str]
    requested_t: NotRequired[float]
    window: NotRequired[float]
    scores: NotRequired[dict[str, float]]
    span: NotRequired[list[float]]
    timestamps: NotRequired[list[float]]
    cols: NotRequired[int]
    render_version: NotRequired[int]
    metadata: NotRequired[dict[str, object]]


class ImageRecord(ImageMetadata):
    path: str
    file: str
    image_id: str
    kind: str
    causes: list[ImageCause]
    tracked: bool
    exists: bool
    cause_id: NotRequired[str]
    cause_type: str
    reason: str | None
