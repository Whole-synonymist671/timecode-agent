"""Bounded, cached decode validation for visual evidence and image caches."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from stat import S_ISREG
from typing import Literal

from .image_types import ImageRecord
from .proc import run

_DECODE_BATCH_SIZE = 32
_DECODE_TIMEOUT_SECONDS = 15.0
type ImageRecordIssueCode = Literal[
    "evidence_provenance_missing",
    "evidence_time_unavailable",
    "evidence_outside_checkpoint",
    "evidence_role_not_verification",
]


@dataclass(frozen=True, slots=True)
class _ImageSignature:
    path: Path
    inode: int
    modified_ns: int
    changed_ns: int
    size: int


def _image_signature(path: Path) -> _ImageSignature | None:
    if path.is_symlink():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if not S_ISREG(stat.st_mode) or stat.st_size < 4:
        return None
    return _ImageSignature(
        path=path,
        inode=stat.st_ino,
        modified_ns=stat.st_mtime_ns,
        changed_ns=stat.st_ctime_ns,
        size=stat.st_size,
    )


def _batch_decode_result(
    signatures: tuple[_ImageSignature, ...],
) -> bool | None:
    command = ["ffmpeg", "-nostdin", "-v", "error"]
    for signature in signatures:
        command.extend(("-i", str(signature.path)))
    for index in range(len(signatures)):
        command.extend(("-map", f"{index}:v:0"))
    command.extend(("-frames:v", "1", "-f", "null", "-"))
    try:
        result = run(
            command,
            timeout=_DECODE_TIMEOUT_SECONDS,
            capture_output=True,
            text=True,
        )
    except (OSError, RuntimeError):
        return None
    return result.returncode == 0


@lru_cache(maxsize=4096)
def _probe_image_batch(
    signatures: tuple[_ImageSignature, ...],
) -> frozenset[Path]:
    decoded = _batch_decode_result(signatures)
    if decoded is None:
        return frozenset()
    if decoded:
        return frozenset(signature.path for signature in signatures)
    if len(signatures) == 1:
        return frozenset()
    midpoint = len(signatures) // 2
    return _probe_image_batch(signatures[:midpoint]) | _probe_image_batch(
        signatures[midpoint:]
    )


def decodable_image_paths(paths: Sequence[Path]) -> frozenset[Path]:
    """Return regular local images that each decode to at least one frame."""
    signatures = tuple(
        signature
        for path in dict.fromkeys(paths)
        if (signature := _image_signature(path)) is not None
    )
    signatures_by_path = {signature.path: signature for signature in signatures}
    decoded: set[Path] = set()
    for offset in range(0, len(signatures), _DECODE_BATCH_SIZE):
        decoded.update(
            _probe_image_batch(signatures[offset : offset + _DECODE_BATCH_SIZE])
        )
    return frozenset(
        path
        for path in decoded
        if _image_signature(path) == signatures_by_path[path]
    )


def is_decodable_image(path: Path) -> bool:
    """True only when a regular local file decodes to at least one video frame."""
    return path in decodable_image_paths((path,))


def image_record_verification_issues(
    record: ImageRecord | None,
    target_span: tuple[float, float],
) -> tuple[ImageRecordIssueCode, ...]:
    """Classify record-level reasons an image cannot verify a checkpoint."""
    if record is None:
        return ("evidence_provenance_missing",)
    issues: list[ImageRecordIssueCode] = []
    if not record["tracked"]:
        issues.append("evidence_provenance_missing")
    if record["kind"] == "filmstrip":
        issues.append("evidence_role_not_verification")
    start, end = target_span
    timestamps = record.get("timestamps")
    if timestamps:
        overlaps = any(start <= timestamp < end for timestamp in timestamps)
    elif (timestamp := record.get("t")) is not None:
        overlaps = start <= timestamp < end
    elif (span := record.get("span")) is not None:
        overlaps = max(span[0], start) < min(span[1], end)
    else:
        overlaps = None
    if overlaps is None:
        issues.append("evidence_time_unavailable")
    elif overlaps is False:
        issues.append("evidence_outside_checkpoint")
    return tuple(issues)
