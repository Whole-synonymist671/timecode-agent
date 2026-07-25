"""Linear-time projection from image events and frame filenames to artifacts."""

from __future__ import annotations

from collections.abc import Iterable

from .image_model import (
    IMAGE_SUFFIXES,
    filename_metadata,
    infer_image_kind,
    normalize_event,
    normalize_image_path,
    record_sort_key,
)
from .image_store import load_provenance_events
from .image_types import ImageCause, ImageEvent, ImageRecord
from .workspace import Workspace, load_json


def _cause_view(event: ImageEvent) -> ImageCause:
    cause: ImageCause = {
        "edge_id": event["edge_id"],
        "cause_id": event["cause_id"],
        "cause_type": event["cause_type"],
        "reason": event["reason"],
        "source": event["source"],
    }
    if "role" in event:
        cause["role"] = event["role"]
    if "requested_t" in event:
        cause["requested_t"] = event["requested_t"]
    if "window" in event:
        cause["window"] = event["window"]
    if "scores" in event:
        cause["scores"] = event["scores"]
    if "span" in event:
        cause["span"] = event["span"]
    if "timestamps" in event:
        cause["timestamps"] = event["timestamps"]
    if "cols" in event:
        cause["cols"] = event["cols"]
    if "render_version" in event:
        cause["render_version"] = event["render_version"]
    if "metadata" in event:
        cause["metadata"] = event["metadata"]
    return cause


def merge_image_events(
    events: Iterable[ImageEvent], *, existing_paths: set[str]
) -> list[ImageRecord]:
    """Merge events in O(edges) using one edge-position map per artifact."""
    by_image_id: dict[str, ImageRecord] = {}
    cause_positions: dict[str, dict[str, int]] = {}
    for event in events:
        image_path = event["path"]
        image_id = event["image_id"]
        event_exists = image_path in existing_paths
        record = by_image_id.get(image_id)
        if record is None:
            record = ImageRecord(
                path=image_path,
                file=event["file"],
                image_id=image_id,
                kind=event["kind"],
                causes=[],
                tracked=event["source"] != "inventory",
                exists=event_exists,
                cause_type=event["cause_type"],
                reason=event["reason"],
            )
            by_image_id[image_id] = record
            cause_positions[image_id] = {}
        else:
            record_exists = bool(record["exists"])
            if (
                event_exists and not record_exists
                or event_exists == record_exists and image_path < record["path"]
            ):
                record["path"] = image_path
                record["file"] = event["file"]
            record["exists"] = record_exists or event_exists
            if event["source"] != "inventory":
                record["tracked"] = True
        if "t" in event:
            record["t"] = event["t"]
        if "span" in event:
            record["span"] = event["span"]
        if "timestamps" in event:
            record["timestamps"] = event["timestamps"]
        if "crop" in event:
            record["crop"] = event["crop"]
        if "cells" in event:
            record["cells"] = event["cells"]
        if "cols" in event:
            record["cols"] = event["cols"]
        if "render_version" in event:
            record["render_version"] = event["render_version"]
        if "role" in event:
            record["role"] = event["role"]
        if "requested_t" in event:
            record["requested_t"] = event["requested_t"]
        if "window" in event:
            record["window"] = event["window"]
        if "scores" in event:
            record["scores"] = event["scores"]
        if "metadata" in event:
            record["metadata"] = event["metadata"]

        if event["source"] != "inventory":
            cause = _cause_view(event)
            positions = cause_positions[image_id]
            prior_index = positions.get(event["edge_id"])
            if prior_index is None:
                positions[event["edge_id"]] = len(record["causes"])
                record["causes"].append(cause)
            else:
                record["causes"][prior_index] = {
                    **record["causes"][prior_index], **cause
                }
            record["cause_id"] = event["cause_id"]
        record["cause_type"] = event["cause_type"]
        record["reason"] = event["reason"]
    return sorted(by_image_id.values(), key=record_sort_key)


def load_image_records(
    ws: Workspace, *, include_untracked: bool = True
) -> list[ImageRecord]:
    """Build an O(files + edges) artifact catalog without opening images."""
    frame_paths: set[str] = set()
    workspace_root = ws.root.resolve(strict=False)
    if ws.frames_dir.is_dir():
        for path in ws.frames_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                relative = path.relative_to(ws.root).as_posix()
                try:
                    path.resolve(strict=False).relative_to(workspace_root)
                    image_path = normalize_image_path(relative)
                except (OSError, RuntimeError, ValueError):
                    continue
                frame_paths.add(image_path)

    events: list[ImageEvent] = []
    legacy = load_json(ws.root / "captures.json")
    if isinstance(legacy, list):
        for raw in legacy:
            if not isinstance(raw, dict):
                continue
            try:
                events.append(normalize_event(ws, raw, source="legacy-captures"))
            except ValueError:
                continue
    events.extend(load_provenance_events(ws))
    tracked_paths = {event["path"] for event in events}
    if include_untracked:
        for image_path in frame_paths - tracked_paths:
            events.append(normalize_event(ws, {
                "path": image_path,
                "kind": infer_image_kind(image_path),
                "cause_type": "legacy-untracked",
                "reason": None,
                **filename_metadata(image_path),
            }, source="inventory"))
    return merge_image_events(events, existing_paths=frame_paths)
