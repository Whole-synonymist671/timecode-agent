"""Image filename grammar, safe path resolution, and stable identities."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Final

from .image_types import ImageMetadata
from .workspace import Workspace

IMAGE_SUFFIXES: Final = {".jpg", ".jpeg", ".png", ".webp"}
_HUMAN_SUFFIX = r"(?:-[^.]+)?"
_FRAME_FILE_RE = re.compile(
    rf"^t(?P<ms>\d{{9}})(?P<crop>_c[0-9a-f]{{8}})?{_HUMAN_SUFFIX}\.jpg$"
)
_FILMSTRIP_FILE_RE = re.compile(
    rf"^strip_(?P<start>\d{{9}})_(?P<end>\d{{9}})_n(?P<count>\d+)"
    rf"(?:_c(?P<cols>\d+)_v(?P<version>\d+))?{_HUMAN_SUFFIX}\.jpg$"
)


def _stable_id(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        _canonical_id_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:16]}"


def _canonical_id_payload(value: object) -> object:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("identity payload numbers must be finite")
        if value == 0:
            return 0
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {
            (
                unicodedata.normalize("NFC", key)
                if isinstance(key, str)
                else key
            ): _canonical_id_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_id_payload(item) for item in value]
    return value


def normalize_image_path(value: str | Path) -> str:
    """Return a safe workspace-relative POSIX image path."""
    normalized = unicodedata.normalize("NFC", str(value).replace("\\", "/"))
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ValueError(f"invalid workspace image path: {value!r}")
    if len(candidate.parts) == 1:
        candidate = PurePosixPath("frames", candidate)
    if candidate.parts[0] != "frames" or candidate.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f"unsupported workspace image path: {value!r}")
    return candidate.as_posix()


@lru_cache(maxsize=512)
def _directory_spellings(
    directory: str,
    inode: int,
    modified_ns: int,
    changed_ns: int,
    case_insensitive: bool,
) -> dict[str, tuple[str, ...]]:
    del inode, modified_ns, changed_ns
    spellings: dict[str, list[str]] = {}
    for child in Path(directory).iterdir():
        normalized = unicodedata.normalize("NFC", child.name)
        key = normalized.casefold() if case_insensitive else normalized
        spellings.setdefault(key, []).append(child.name)
    return {key: tuple(names) for key, names in spellings.items()}


def _existing_spelling(
    parent: Path, requested_name: str, *, case_insensitive: bool
) -> str:
    try:
        stat = parent.stat()
    except FileNotFoundError:
        return requested_name
    spellings = _directory_spellings(
        str(parent),
        stat.st_ino,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        case_insensitive,
    )
    normalized = unicodedata.normalize("NFC", requested_name)
    key = normalized.casefold() if case_insensitive else normalized
    matches = spellings.get(key, ())
    if requested_name in matches:
        return requested_name
    return matches[0] if len(matches) == 1 else requested_name


def resolve_image_path(ws: Workspace, value: str | Path) -> tuple[str, Path]:
    """Resolve one image inside the workspace, rejecting traversal and symlinks out."""
    image_path = normalize_image_path(value)
    workspace_root = ws.root.resolve(strict=False)
    case_insensitive = _workspace_is_case_insensitive(str(ws.root))
    current = ws.root
    actual_parts: list[str] = []
    try:
        for part in PurePosixPath(image_path).parts:
            actual = _existing_spelling(
                current, part, case_insensitive=case_insensitive
            )
            actual_parts.append(unicodedata.normalize("NFC", actual))
            current /= actual
        resolved = current.resolve(strict=False)
        resolved.relative_to(workspace_root)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(f"image path escapes workspace: {value!r}") from error
    return PurePosixPath(*actual_parts).as_posix(), resolved


@lru_cache(maxsize=256)
def _workspace_is_case_insensitive(root: str) -> bool:
    manifest = Path(root) / "manifest.json"
    alternate = manifest.with_name(manifest.name.swapcase())
    try:
        return alternate.samefile(manifest)
    except OSError:
        return False


def _workspace_identity_name(ws: Workspace) -> str:
    name = unicodedata.normalize("NFC", ws.root.name)
    if _workspace_is_case_insensitive(str(ws.root)):
        return unicodedata.normalize("NFC", name.casefold())
    return name


def _identity_image_path(ws: Workspace, image_path: str | Path) -> str:
    normalized = normalize_image_path(image_path)
    if _workspace_is_case_insensitive(str(ws.root)):
        return unicodedata.normalize("NFC", normalized.casefold())
    return normalized


def image_identity(ws: Workspace, image_path: str | Path) -> str:
    """Deterministic vault-local ID, stable when the corpus parent moves."""
    return _stable_id(
        "img", [_workspace_identity_name(ws), _identity_image_path(ws, image_path)]
    )


def cause_identity(ws: Workspace, cause_type: str, context: object) -> str:
    """Deterministic ID shared by every image caused by one decision."""
    return _stable_id(
        "cause", [_workspace_identity_name(ws), cause_type, context]
    )


def infer_image_kind(image_path: str) -> str:
    file = PurePosixPath(image_path).name
    if file.startswith("strip_"):
        return "filmstrip"
    frame_match = _FRAME_FILE_RE.match(file)
    if frame_match and frame_match.group("crop"):
        return "frame-crop"
    return "frame"


def strip_cell_midpoints(
    start: float, end: float, count: int
) -> list[float] | None:
    """filmstrip.py mids와 동일한 셀 중점 — 문법 밖 geometry면 None.

    count 상한은 CLI 제약 1..16과 동일 — 조작된 값의 팽창 방지.
    """
    if not (start < end and 1 <= count <= 16):
        return None
    return [start + (i + 0.5) * (end - start) / count for i in range(count)]


def filename_metadata(image_path: str) -> ImageMetadata:
    file = PurePosixPath(image_path).name
    frame_match = _FRAME_FILE_RE.match(file)
    if frame_match:
        return {"t": int(frame_match.group("ms")) / 1000}
    strip_match = _FILMSTRIP_FILE_RE.match(file)
    if not strip_match:
        return {}
    start = int(strip_match.group("start")) / 1000
    end = int(strip_match.group("end")) / 1000
    count = int(strip_match.group("count"))
    metadata: ImageMetadata = {
        "span": [start, end],
        "cells": count,
    }
    midpoints = strip_cell_midpoints(start, end, count)
    if midpoints is not None:
        # 프로비넌스 이벤트가 없는 레거시 스트립도 느슨한 범위 중첩이 아닌
        # 셀 중점 규칙으로 시간 정합을 판정한다.
        metadata["timestamps"] = midpoints
    if strip_match.group("cols"):
        metadata["cols"] = int(strip_match.group("cols"))
        metadata["render_version"] = int(strip_match.group("version"))
    return metadata
