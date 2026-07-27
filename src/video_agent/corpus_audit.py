"""Read-only, deterministic readiness aggregation across video workspaces."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

from .access_log import AccessSummary, summarize_access_log
from .corpus_projection import narrative_head
from .narrative_audit import QualifierDropIssue, audit_narrative_qualifiers
from .relation_candidates import RelationCandidate, candidates_from_checkpoints
from .search import corpus_root, find_workspaces
from .status import _workspace_status_snapshot
from .verification_types import VerificationIssue
from .wiki_audit import (
    WIKI_INDEX_BYTE_LIMIT as WIKI_INDEX_BYTE_LIMIT,
)
from .wiki_audit import (
    ImprovementCandidates,
    WikiIntegrityAudit,
    WikiLayerAudit,
    audit_wiki_integrity,
    audit_wiki_layer,
    collect_improvements,
)
from .workspace import Workspace


class CorpusWorkspaceAudit(TypedDict):
    """Readiness and evidence totals for one manifest-backed workspace."""

    path: str
    readiness: str
    readiness_legacy: str
    verified_ratio: float
    supported_verified_ratio: float
    terminal_count: int
    supported_count: int
    issue_counts: dict[str, int]
    issues: list[VerificationIssue]
    qualifier_drops: list[QualifierDropIssue]


class CorpusAudit(TypedDict):
    """Stable aggregate readiness projection for a discovered workspace corpus."""

    workspace_count: int
    terminal_count: int
    supported_count: int
    issue_counts: dict[str, int]
    qualifier_drop_total: int
    readiness_counts: dict[str, int]
    readiness_legacy_counts: dict[str, int]
    verification_levels_declared: dict[str, int]
    verification_levels_grounded: dict[str, int]
    access: AccessSummary
    workspaces: list[CorpusWorkspaceAudit]
    relation_candidate_total: int
    relation_candidates: list[RelationCandidate]
    wiki: WikiLayerAudit | None
    wiki_integrity: "WikiIntegrityAudit | None"
    improvements: "ImprovementCandidates | None"


class CorpusAuditError(ValueError):
    """No manifest-backed workspaces were found under the requested roots."""

    def __init__(self, roots: list[str] | None) -> None:
        self.roots = tuple(roots or ["./va-out"])
        super().__init__(str(self))

    def __str__(self) -> str:
        return "no workspaces found (looked in: " + ", ".join(self.roots) + ")"


def _sorted_counts(counts: Counter[str]) -> dict[str, int]:
    """Return a normal dictionary whose insertion order is deterministic."""
    return dict(sorted(counts.items()))


# 행별 상세 목록만 자르는 표시 상한 — 총계(qualifier_drop_total)는 무절단.
_QUALIFIER_DETAIL_CAP = 20
# 관계 후보도 같은 규약 — 총계는 무절단, 목록만 자른다.
_RELATION_CANDIDATE_DETAIL_CAP = 20


def audit_corpus(
    roots: list[str] | None = None,
    *,
    workspace_paths: Sequence[Path] | None = None,
    projection_root: Path | None = None,
) -> CorpusAudit:
    """Aggregate status snapshots without writing any discovered workspace."""
    discovered = (
        list(workspace_paths)
        if workspace_paths is not None
        else find_workspaces(roots)
    )
    paths = sorted(
        {path.resolve() for path in discovered},
        key=lambda path: str(path),
    )
    if not paths:
        raise CorpusAuditError(roots)
    root = (
        Path(projection_root).resolve()
        if projection_root is not None
        else corpus_root(roots)
    )

    terminal_count = 0
    supported_count = 0
    issue_counts: Counter[str] = Counter()
    readiness_counts: Counter[str] = Counter()
    readiness_legacy_counts: Counter[str] = Counter()
    verification_levels_declared: Counter[str] = Counter()
    verification_levels_grounded: Counter[str] = Counter()
    narrative_missing: list[str] = []
    qualifier_drop_total = 0
    workspaces: list[CorpusWorkspaceAudit] = []
    candidates: list[RelationCandidate] = []
    for path in paths:
        ws = Workspace.load(path)
        snapshot = _workspace_status_snapshot(ws)
        status = snapshot.status
        qualifier_drops = audit_narrative_qualifiers(
            ws, snapshot.terminal_checkpoints
        )
        qualifier_drop_total += len(qualifier_drops)
        candidates.extend(
            candidates_from_checkpoints(path.name, snapshot.checkpoints))
        if (status["readiness"]["status"] == "converged"
                and narrative_head(ws) is None):
            narrative_missing.append(path.name)
        for levels in snapshot.verification.checkpoint_levels:
            verification_levels_declared.update([levels.declared])
            verification_levels_grounded.update([levels.grounded])
        evidence = status["verification_audit"]
        readiness = status["readiness"]["status"]
        readiness_legacy = status["readiness_legacy"]["status"]
        local_issue_counts = Counter(issue["code"] for issue in evidence["issues"])
        terminal_count += evidence["terminal_count"]
        supported_count += evidence["supported_count"]
        issue_counts.update(local_issue_counts)
        readiness_counts.update([readiness])
        readiness_legacy_counts.update([readiness_legacy])
        workspaces.append(
            {
                "path": str(path),
                "readiness": readiness,
                "readiness_legacy": readiness_legacy,
                "verified_ratio": status["verified_ratio"],
                "supported_verified_ratio": evidence["supported_verified_ratio"],
                "terminal_count": evidence["terminal_count"],
                "supported_count": evidence["supported_count"],
                "issue_counts": _sorted_counts(local_issue_counts),
                "issues": evidence["issues"],
                "qualifier_drops": qualifier_drops[:_QUALIFIER_DETAIL_CAP],
            }
        )
    return {
        "workspace_count": len(workspaces),
        "terminal_count": terminal_count,
        "supported_count": supported_count,
        "issue_counts": _sorted_counts(issue_counts),
        "qualifier_drop_total": qualifier_drop_total,
        "readiness_counts": _sorted_counts(readiness_counts),
        "readiness_legacy_counts": _sorted_counts(readiness_legacy_counts),
        "verification_levels_declared": _sorted_counts(verification_levels_declared),
        "verification_levels_grounded": _sorted_counts(verification_levels_grounded),
        # 근거 품질 옆에 소비량을 둔다 — 아무도 읽지 않는 코퍼스는
        # 근거가 아무리 좋아도 지식으로 기능하지 않는다.
        "access": summarize_access_log(root),
        "workspaces": workspaces,
        "relation_candidate_total": len(candidates),
        "relation_candidates": candidates[:_RELATION_CANDIDATE_DETAIL_CAP],
        "wiki": audit_wiki_layer(root),
        "wiki_integrity": audit_wiki_integrity(root),
        "improvements": collect_improvements(
            root, narrative_missing),
    }
