"""Locked append-only storage for canonical image provenance events."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Final
from weakref import WeakKeyDictionary

from .fsio import write_text_atomic
from .image_model import normalize_event, normalize_image_path, record_sort_key
from .image_types import ImageEvent, ImageEventInput
from .workspace import Workspace, load_json
from .workspace_lock import stable_workspace_lock

PROVENANCE_FILENAME: Final = "image-provenance.jsonl"

type _Signature = tuple[int, int, int, int] | None
type _EventCache = tuple[_Signature, list[ImageEvent], bool, int]
_EVENT_CACHE: WeakKeyDictionary[Workspace, _EventCache] = WeakKeyDictionary()


@contextmanager
def _provenance_lock(ws: Workspace, *, exclusive: bool) -> Iterator[None]:
    with stable_workspace_lock(
        ws.root,
        ws.image_provenance_lock_path,
        exclusive=exclusive,
    ):
        yield


def _provenance_path(ws: Workspace) -> Path:
    return ws.root / PROVENANCE_FILENAME


def _signature(ws: Workspace) -> _Signature:
    try:
        stat = _provenance_path(ws).stat()
    except FileNotFoundError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


def _parse_event_lines(
    ws: Workspace, lines: list[bytes], *, first_lineno: int
) -> list[ImageEvent]:
    events: list[ImageEvent] = []
    for offset, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            raw = json.loads(line.decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("event must be an object")
            events.append(normalize_event(ws, raw, preserve_persisted_id=True))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            print(
                "warning: skipping corrupt image-provenance.jsonl line "
                f"{first_lineno + offset}",
                file=sys.stderr,
            )
    return events


def _load_events_unlocked(ws: Workspace) -> list[ImageEvent]:
    signature = _signature(ws)
    cached = _EVENT_CACHE.get(ws)
    if cached is not None and cached[0] == signature:
        return cached[1]
    if signature is None:
        _EVENT_CACHE[ws] = (None, [], True, 0)
        return []
    if (
        cached is not None
        and cached[0] is not None
        and cached[2]
        and cached[0][0] == signature[0]
        and cached[0][3] < signature[3]
    ):
        with _provenance_path(ws).open("rb") as ledger:
            ledger.seek(cached[0][3])
            delta = ledger.read()
        lines = delta.splitlines()
        events = cached[1] + _parse_event_lines(
            ws, lines, first_lineno=cached[3] + 1
        )
        _EVENT_CACHE[ws] = (
            signature,
            events,
            delta.endswith(b"\n"),
            cached[3] + len(lines),
        )
        return events
    contents = _provenance_path(ws).read_bytes()
    lines = contents.splitlines()
    events = _parse_event_lines(ws, lines, first_lineno=1)
    _EVENT_CACHE[ws] = (
        signature,
        events,
        not contents or contents.endswith(b"\n"),
        len(lines),
    )
    return events


def load_provenance_events(ws: Workspace) -> list[ImageEvent]:
    with _provenance_lock(ws, exclusive=False):
        return [deepcopy(event) for event in _load_events_unlocked(ws)]


def append_image_provenance(
    ws: Workspace, entries: Sequence[ImageEventInput]
) -> list[ImageEvent]:
    """Append only unseen artifact-cause edges under a POSIX file lock."""
    incoming = [normalize_event(ws, entry) for entry in entries]
    if not incoming:
        return []
    with _provenance_lock(ws, exclusive=True):
        existing = _load_events_unlocked(ws)
        cached = _EVENT_CACHE.get(ws)
        known = {event["edge_id"] for event in existing}
        missing: list[ImageEvent] = []
        for event in incoming:
            if event["edge_id"] not in known:
                known.add(event["edge_id"])
                missing.append(event)
        if not missing:
            return []
        path = _provenance_path(ws)
        prefix = ""
        if path.is_file() and path.stat().st_size:
            with path.open("rb") as ledger:
                ledger.seek(-1, 2)
                if ledger.read(1) not in (b"\n", b"\r"):
                    prefix = "\n"
        with path.open("a", encoding="utf-8") as ledger:
            ledger.write(prefix)
            ledger.writelines(
                json.dumps(event, ensure_ascii=False) + "\n" for event in missing
            )
        previous_lines = cached[3] if cached is not None else len(existing)
        _EVENT_CACHE[ws] = (
            _signature(ws), existing + missing, True, previous_lines + len(missing)
        )
        return missing


def record_legacy_capture_reasons(
    ws: Workspace, entries: Sequence[ImageEventInput]
) -> None:
    """Maintain the old reason snapshot for explicit-reason consumers only."""
    reasoned = [entry for entry in entries if entry.get("reason")]
    if not reasoned:
        return
    path = ws.root / "captures.json"
    with _provenance_lock(ws, exclusive=True):
        existing = load_json(path)
        if path.is_file() and not isinstance(existing, list):
            print(
                "warning: preserving corrupt captures.json; canonical image "
                "provenance was still recorded",
                file=sys.stderr,
            )
            return
        by_file: dict[str, dict[str, object]] = {
            item["file"]: item
            for item in existing or []
            if isinstance(item, dict) and isinstance(item.get("file"), str)
        }
        for entry in reasoned:
            path_value = entry.get("path") or entry.get("file")
            if not isinstance(path_value, str):
                continue
            file = PurePosixPath(normalize_image_path(path_value)).name
            previous = by_file.get(file, {})
            compatible = {
                key: value for key, value in entry.items()
                if key not in {"schema", "path", "source", "edge_id", "causes"}
            }
            by_file[file] = {**previous, **compatible, "file": file}
        ordered = sorted(by_file.values(), key=record_sort_key)
        serialized = json.dumps(ordered, ensure_ascii=False, indent=1)
        if not path.is_file() or path.read_text(encoding="utf-8") != serialized:
            write_text_atomic(path, serialized)
