"""Canonical workspace discovery constrained to caller-supplied roots."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from .workspace import load_json

DEFAULT_WORKSPACE_ROOT: Final = Path("./va-out")


class MultipleProjectionRootsError(ValueError):
    """Raised when no explicitly supplied root can own every projection."""

    roots: tuple[Path, ...]

    def __init__(self, roots: tuple[Path, ...]) -> None:
        self.roots = roots
        rendered = ", ".join(str(root) for root in roots)
        super().__init__(
            "multiple projection roots are ambiguous "
            f"({rendered}); pass one common corpus root",
        )


def corpus_root(roots: list[str] | None) -> Path:
    """Return one canonical explicit root that can own every projection."""
    candidates: list[Path] = []
    for raw in roots or [str(DEFAULT_WORKSPACE_ROOT)]:
        resolved = Path(raw).resolve()
        candidate = (
            resolved.parent
            if (resolved / "manifest.json").is_file()
            else resolved
        )
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        if all(other.is_relative_to(candidate) for other in candidates):
            return candidate
    raise MultipleProjectionRootsError(tuple(candidates))


def find_workspaces(
    roots: list[str] | None,
    *,
    include_building: bool = False,
) -> list[Path]:
    """Return canonical workspaces, omitting incomplete ingests by default."""
    requested_roots = (
        [Path(raw) for raw in roots]
        if roots
        else [DEFAULT_WORKSPACE_ROOT]
    )
    workspaces: list[Path] = []
    seen: set[Path] = set()

    for requested_root in requested_roots:
        if not requested_root.is_dir():
            continue
        boundary = requested_root.resolve()
        if (boundary / "manifest.json").is_file():
            candidates = [boundary]
        else:
            manifests = sorted(
                (
                    *boundary.glob("*/manifest.json"),
                    *boundary.glob("*/*/manifest.json"),
                ),
            )
            candidates = [manifest.parent for manifest in manifests]

        for candidate_path in candidates:
            candidate = candidate_path.resolve()
            if not candidate.is_relative_to(boundary):
                continue
            manifest_path = candidate / "manifest.json"
            if candidate in seen or not manifest_path.is_file():
                continue
            manifest = load_json(manifest_path)
            if (
                not include_building
                and isinstance(manifest, dict)
                and manifest.get("ingest_state") == "building"
            ):
                continue
            seen.add(candidate)
            workspaces.append(candidate)

    return workspaces
