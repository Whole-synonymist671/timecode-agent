"""Typed contracts shared by verification audit projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from .checkpoint_schema import CheckpointObject, ValidatedCheckpoint
from .image_validation import ImageRecordIssueCode

type VisualEvidenceIssueCode = ImageRecordIssueCode | Literal[
    "artifact_unavailable",
]
type VerificationIssueCode = VisualEvidenceIssueCode | Literal[
    "correction_note_missing",
    "legacy_unstructured",
    "missing_support",
]
type PromotableEntry = tuple[ValidatedCheckpoint, CheckpointObject]
type CheckpointEntries = list[PromotableEntry]
type SegmentIdKey = tuple[str, str]
type TranscriptSegmentIndex = dict[SegmentIdKey, list[tuple[float, float]]]
type VerificationLevel = Literal[
    "cross_modal",
    "visual_only",
    "transcript_only",
    "unsupported",
]


class VerificationIssue(TypedDict):
    checkpoint_id: str
    code: VerificationIssueCode
    artifact: NotRequired[str]


class VerificationAudit(TypedDict):
    terminal_count: int
    supported_count: int
    supported_verified_ratio: float
    issues: list[VerificationIssue]


@dataclass(frozen=True, slots=True)
class CheckpointVerificationLevels:
    """Declared legacy level beside accepted visual/transcript grounding."""

    checkpoint_id: str
    declared: VerificationLevel
    grounded: VerificationLevel


@dataclass(frozen=True, slots=True)
class VerificationSnapshot:
    """Public audit plus internal per-checkpoint modality decisions."""

    audit: VerificationAudit
    checkpoint_levels: tuple[CheckpointVerificationLevels, ...]
    supported_checkpoint_ids: frozenset[str]
