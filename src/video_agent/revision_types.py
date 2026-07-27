"""Dependency-light types shared by workspace lifecycle and revision modules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, TypedDict

type RevisionValue = (
    str
    | int
    | float
    | bool
    | None
    | Sequence[RevisionValue]
    | Mapping[str, RevisionValue]
)
type RevisionRecord = dict[str, RevisionValue]


class RevisionBindings(TypedDict):
    source_revision_id: str
    transcript_revision_id: str
    timing_revision_id: str


REVISION_FIELDS: Final = (
    "source_revision_id",
    "transcript_revision_id",
    "timing_revision_id",
)


class RevisionBindingError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)

    def __str__(self) -> str:
        return self.detail
