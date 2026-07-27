"""Crash-recoverable transaction journal for canonical diarization rewrites."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final, TypedDict

from .fsio import write_text_atomic
from .workspace_lock import stable_workspace_lock

DIARIZATION_TRANSACTION_FILENAME: Final = ".diarization-transaction.json"
_SCHEMA_VERSION: Final = 1


class _DiarizationSnapshot(TypedDict):
    existed: bool
    text: str | None


class _DiarizationJournal(TypedDict):
    schema_version: int
    transaction: str
    manifest_text: str
    transcript_text: str
    diarization: _DiarizationSnapshot


def journal_path(root: Path) -> Path:
    return Path(root) / DIARIZATION_TRANSACTION_FILENAME


def _fsync_directory(root: Path) -> None:
    directory_fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _unlink_durable(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _load_journal(path: Path) -> _DiarizationJournal:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "diarization transaction journal is unreadable; preserve the "
            "workspace for manual recovery"
        ) from error
    if not isinstance(raw, dict):
        raise RuntimeError(
            "diarization transaction journal is invalid; preserve the "
            "workspace for manual recovery"
        )
    diarization = raw.get("diarization")
    valid_diarization = (
        isinstance(diarization, dict)
        and isinstance(diarization.get("existed"), bool)
        and (
            isinstance(diarization.get("text"), str)
            if diarization.get("existed") is True
            else diarization.get("text") is None
        )
    )
    if not (
        raw.get("schema_version") == _SCHEMA_VERSION
        and raw.get("transaction") == "diarization"
        and isinstance(raw.get("manifest_text"), str)
        and isinstance(raw.get("transcript_text"), str)
        and valid_diarization
    ):
        raise RuntimeError(
            "diarization transaction journal is invalid; preserve the "
            "workspace for manual recovery"
        )
    assert isinstance(diarization, dict)
    existed = diarization.get("existed")
    text = diarization.get("text")
    assert isinstance(existed, bool)
    assert text is None or isinstance(text, str)
    return {
        "schema_version": _SCHEMA_VERSION,
        "transaction": "diarization",
        "manifest_text": raw["manifest_text"],
        "transcript_text": raw["transcript_text"],
        "diarization": {
            "existed": existed,
            "text": text,
        },
    }


def begin_diarization_transaction(
    root: Path,
    *,
    manifest_text: str,
    transcript_text: str,
    diarization_text: str | None,
) -> None:
    """Durably save the last consistent canonical state before any rewrite."""
    path = journal_path(root)
    if path.exists():
        raise RuntimeError(
            "pending diarization transaction must be recovered before retry"
        )
    journal: _DiarizationJournal = {
        "schema_version": _SCHEMA_VERSION,
        "transaction": "diarization",
        "manifest_text": manifest_text,
        "transcript_text": transcript_text,
        "diarization": {
            "existed": diarization_text is not None,
            "text": diarization_text,
        },
    }
    write_text_atomic(
        path,
        json.dumps(journal, ensure_ascii=False, sort_keys=True),
    )


def rollback_diarization_transaction_locked(root: Path) -> bool:
    """Restore a journal snapshot; caller must hold the exclusive operation lock."""
    workspace_root = Path(root)
    path = journal_path(workspace_root)
    if not path.is_file():
        return False
    journal = _load_journal(path)
    write_text_atomic(
        workspace_root / "transcript.json",
        journal["transcript_text"],
    )
    diarization_path = workspace_root / "diarization.json"
    snapshot = journal["diarization"]
    if snapshot["existed"]:
        text = snapshot["text"]
        assert isinstance(text, str)
        write_text_atomic(diarization_path, text)
    else:
        _unlink_durable(diarization_path)
    write_text_atomic(
        workspace_root / "manifest.json",
        journal["manifest_text"],
    )
    _unlink_durable(path)
    return True


def recover_pending_diarization_transaction(
    root: Path,
    workspace_lock_path: Path,
) -> bool:
    """Recover a crashed rewrite after its process releases the operation lock."""
    workspace_root = Path(root)
    if not journal_path(workspace_root).exists():
        return False
    with stable_workspace_lock(
        workspace_root,
        Path(workspace_lock_path),
        exclusive=True,
    ):
        return rollback_diarization_transaction_locked(workspace_root)


def complete_diarization_transaction(root: Path) -> None:
    """Durably commit by removing the rollback journal after publication."""
    path = journal_path(root)
    if not path.is_file():
        raise RuntimeError("diarization transaction journal disappeared")
    _load_journal(path)
    _unlink_durable(path)
