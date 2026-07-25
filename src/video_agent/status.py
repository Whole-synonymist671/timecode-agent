"""Single-snapshot workspace readiness projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .checkpoint_schema import (
    CheckpointObject,
    ConvergenceMetrics,
    ConvergenceResult,
    CoverageStatus,
)
from .checkpoint_store import _load_checkpoint_entries
from .checkpoints import _coverage_status_from_entries, convergence_gate
from .verification import (
    _promotable_entries_from_entries,
    _verification_snapshot_from_entries,
)
from .verification_types import VerificationAudit, VerificationSnapshot
from .workspace import Workspace


class ReadinessV2(ConvergenceResult):
    """Grounded readiness that identifies its evidence basis."""

    version: Literal[2]
    verified_basis: Literal["supported_verified_ratio"]


class WorkspaceStatus(CoverageStatus):
    """Coverage, evidence audit, and readiness from one ledger snapshot.

    `readiness` is the canonical grounded gate (CONTEXT.md 용어 정본);
    the coverage-only legacy gate rides along as `readiness_legacy`.
    """

    verification_audit: VerificationAudit
    readiness: ReadinessV2


@dataclass(frozen=True, slots=True)
class _WorkspaceStatusSnapshot:
    status: WorkspaceStatus
    verification: VerificationSnapshot
    # 같은 원장 읽기에서 나온 터미널 체크포인트 — 서사 감사처럼 본문
    # 텍스트가 필요한 소비자가 원장을 다시 읽지 않게 한다.
    terminal_checkpoints: tuple[CheckpointObject, ...]
    supported_checkpoints: tuple[CheckpointObject, ...]
    # 전체 원장 — 관계 후보처럼 터미널 여부와 무관하게 모든 체크포인트를
    # 훑어야 하는 소비자가 원장을 다시 읽지 않게 한다.
    checkpoints: tuple[CheckpointObject, ...]


def _workspace_status_snapshot(ws: Workspace) -> _WorkspaceStatusSnapshot:
    """Project public status and internal modalities from one ledger read."""
    entries = _load_checkpoint_entries(ws)
    coverage = _coverage_status_from_entries(ws, entries)
    verification = _verification_snapshot_from_entries(ws, entries)
    terminal_entries = _promotable_entries_from_entries(entries)
    audit = verification.audit
    metrics: ConvergenceMetrics = {
        "covered_ratio": coverage["covered_ratio"],
        "verified_ratio": audit["supported_verified_ratio"],
        "gaps": coverage["gaps"],
        "mean_confidence": coverage["mean_confidence"],
    }
    grounded = convergence_gate(
        metrics,
        terminal_support_complete=(
            audit["supported_count"] == audit["terminal_count"]
        ),
    )
    readiness: ReadinessV2 = {
        **grounded,
        "version": 2,
        "verified_basis": "supported_verified_ratio",
    }
    status: WorkspaceStatus = {
        **coverage,
        "verification_audit": audit,
        "readiness": readiness,
    }
    return _WorkspaceStatusSnapshot(
        status=status,
        verification=verification,
        terminal_checkpoints=tuple(checkpoint for _, checkpoint in terminal_entries),
        supported_checkpoints=tuple(
            checkpoint
            for validated, checkpoint in terminal_entries
            if validated.checkpoint_id in verification.supported_checkpoint_ids
        ),
        checkpoints=tuple(checkpoint for _, checkpoint in entries),
    )


def workspace_status(ws: Workspace) -> WorkspaceStatus:
    """Project coverage and evidence-grounded readiness from one ledger read."""
    return _workspace_status_snapshot(ws).status
