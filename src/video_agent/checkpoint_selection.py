"""Shared latest-checkpoint selection for export projections."""

from __future__ import annotations

from .checkpoints import load_checkpoints
from .workspace import Workspace


def selected_checkpoints(
    ws: Workspace, ids: list[str] | None
) -> list[dict]:
    """ids 미지정=원장 전체(시간순). 지정 시 **지정 순서**로 반환한다 —
    러프컷 인계(edl·otio·fcpxml)에서 지정 순서가 곧 컷 순서다."""
    checkpoints = load_checkpoints(ws)
    if ids is None:
        return checkpoints
    by_id = {
        checkpoint_id: checkpoint
        for checkpoint in checkpoints
        if isinstance((checkpoint_id := checkpoint.get("id")), str)
    }
    missing = set(ids) - set(by_id)
    if missing:
        raise ValueError(f"unknown checkpoint ids: {sorted(missing)}")
    return [by_id[checkpoint_id] for checkpoint_id in dict.fromkeys(ids)]
