"""Fail-closed workspace creation and retry lifecycle guards."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from .fsio import write_text_atomic
from .revision_types import (
    REVISION_FIELDS,
    RevisionBindingError,
    RevisionValue,
)
from .workspace_lock import stable_workspace_lock

_DURABLE_EVIDENCE: Final = (
    "transcript.json",
    "transcript.srt",
    "words.json",
    "checkpoints.jsonl",
    "sequences.jsonl",
    "image-provenance.jsonl",
    "corrections.jsonl",
    "captures.json",
    "ocr_transcript.json",
    "highlights.json",
    "scenes.json",
    "diarization.json",
    "audio_events.json",
)
_DURABLE_DIRECTORIES: Final = ("frames", "clips", "wiki", "conflicts")
_RESUME_BLOCKING_EVIDENCE: Final = tuple(
    name
    for name in _DURABLE_EVIDENCE
    if name not in {"transcript.json", "transcript.srt", "words.json"}
)


def _requested_source_identity(requested_source: Path | str) -> str:
    source = str(requested_source)
    if source.startswith(("http://", "https://")):
        return source
    return str(Path(requested_source).resolve())


def _has_durable_content(path: Path) -> bool:
    if path.is_symlink():
        return True
    if not path.exists():
        return False
    if not path.is_dir():
        return True
    try:
        next(path.iterdir())
    except StopIteration:
        return False
    except OSError:
        return True
    return True


def _material_names(
    root: Path,
    files: Sequence[str],
    directories: Sequence[str],
) -> list[str]:
    material = [
        name for name in files if _has_durable_content(root / name)
    ]
    material.extend(
        name for name in directories if _has_durable_content(root / name)
    )
    return material


def assert_building_workspace_resume_allowed(
    root: Path,
    manifest: Mapping[str, RevisionValue],
    candidate_source: Path | str,
) -> None:
    """Validate one lower-level create against its building ingest marker."""
    workspace_root = Path(root)
    expected_source = manifest.get("ingest_requested_source")
    candidate_text = str(candidate_source)
    source_matches = False
    if isinstance(expected_source, str) and expected_source:
        if expected_source.startswith(("http://", "https://")):
            if candidate_text.startswith(("http://", "https://")):
                source_matches = candidate_text == expected_source
            else:
                candidate_path = Path(candidate_source).resolve()
                source_matches = candidate_path.is_relative_to(
                    workspace_root.resolve()
                )
        else:
            source_matches = expected_source == _requested_source_identity(
                candidate_source
            )
    if not source_matches:
        raise RevisionBindingError(
            "incomplete workspace belongs to a different source; retry the "
            "original source or use a fresh `-o` path"
        )
    blocking = _material_names(
        workspace_root,
        _RESUME_BLOCKING_EVIDENCE,
        _DURABLE_DIRECTORIES,
    )
    if blocking:
        raise RevisionBindingError(
            "incomplete workspace contains durable evidence "
            f"({', '.join(blocking)}); preserve it and use a fresh `-o` path"
        )


def assert_ingest_target_unused(
    root: Path,
    requested_source: Path | str | None = None,
) -> None:
    """Reject completed workspaces; admit only a safe same-source retry."""
    workspace_root = Path(root)
    manifest = workspace_root / "manifest.json"
    if manifest.is_file():
        try:
            existing = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RevisionBindingError(
                "existing workspace manifest is unreadable; preserve it and "
                "use a fresh `-o` path"
            ) from error
        if not isinstance(existing, dict):
            raise RevisionBindingError(
                "existing workspace manifest is invalid; preserve it and use "
                "a fresh `-o` path"
            )
        has_revision = any(
            field in existing for field in (*REVISION_FIELDS, "source_content_hash")
        )
        if existing.get("ingest_state") == "building" and not has_revision:
            if requested_source is None:
                raise RevisionBindingError(
                    "incomplete workspace belongs to a different source; "
                    "retry the original source or use a fresh `-o` path"
                )
            assert_building_workspace_resume_allowed(
                workspace_root,
                existing,
                requested_source,
            )
            return
        raise RevisionBindingError(
            "workspace already exists; resume with `va brief` or use a fresh "
            "`-o` path for a new source/transcript revision"
        )
    orphaned = _material_names(
        workspace_root,
        _DURABLE_EVIDENCE,
        _DURABLE_DIRECTORIES,
    )
    if orphaned:
        raise RevisionBindingError(
            "workspace contains durable evidence without a manifest "
            f"({', '.join(orphaned)}); preserve it and use a fresh `-o` path"
        )


def _write_building_manifest(
    root: Path,
    requested_source: Path | str,
) -> None:
    """Publish a building marker while the caller holds the operation lease."""
    manifest = {
        "ingest_state": "building",
        "ingest_requested_source": _requested_source_identity(requested_source),
    }
    write_text_atomic(
        Path(root) / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2),
    )


def begin_ingest(root: Path, requested_source: Path | str) -> None:
    """Atomically guard and publish the explicit retryable ingest marker."""
    workspace_root = Path(root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    manifest_path = workspace_root / "manifest.json"
    workspace_lock_path = workspace_root / ".workspace.lock"
    if not manifest_path.is_file():
        workspace_lock_path.touch(mode=0o600, exist_ok=True)
    with stable_workspace_lock(
        workspace_root,
        workspace_lock_path,
        exclusive=True,
    ):
        assert_ingest_target_unused(workspace_root, requested_source)
        _write_building_manifest(workspace_root, requested_source)
