"""Bounded, cached decode validation for visual evidence and image caches."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from stat import S_ISREG
from typing import Literal

from .cache_paths import cache_root
from .fsio import write_text_atomic
from .image_types import ImageRecord
from .proc import run

_DECODE_BATCH_SIZE = 32
_DECODE_TIMEOUT_SECONDS = 15.0
DECODE_CACHE_FILENAME = "decode-probe.json"
# 상한 초과분은 가장 먼저 들어온 항목부터 버린다(FIFO). 전량 폐기하면
# 캐시가 상한에 닿은 뒤로는 새 이미지 하나를 기록할 때마다 디스크가
# 비워져, 프로세스 경계를 넘겠다는 캐시의 목적이 조용히 사라진다
# (적대검증 2026-07-27에 cap 축소 재현으로 확인).
_DECODE_CACHE_MAX_ENTRIES = 50_000
# 삽입 순서를 보존하는 집합으로 쓴다 — 값은 의미 없다.
_decode_cache: dict[str, None] | None = None
type ImageRecordIssueCode = Literal[
    "evidence_provenance_missing",
    "evidence_time_unavailable",
    "evidence_outside_checkpoint",
    "evidence_role_not_verification",
]


@dataclass(frozen=True, slots=True)
class _ImageSignature:
    path: Path
    device: int
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
        device=stat.st_dev,
        inode=stat.st_ino,
        modified_ns=stat.st_mtime_ns,
        changed_ns=stat.st_ctime_ns,
        size=stat.st_size,
    )


def _signature_key(signature: _ImageSignature) -> str:
    """경로가 아니라 파일 실체를 가리키는 키 — 하드링크·이동에 안정적.

    ``device``까지 넣는 이유: inode 번호는 볼륨 안에서만 유일하다.
    """
    return (
        f"{signature.device}:{signature.inode}:{signature.modified_ns}:"
        f"{signature.changed_ns}:{signature.size}"
    )


def _decode_cache_path() -> Path:
    return cache_root() / DECODE_CACHE_FILENAME


def reset_decode_cache() -> None:
    """프로세스 내 캐시를 비운다 — 테스트가 새 프로세스를 흉내낼 때 쓴다."""
    global _decode_cache
    _decode_cache = None
    _probe_image_batch.cache_clear()


def _read_decode_cache_file() -> dict[str, None]:
    """디스크 순서를 그대로 보존해 읽는다 — 그 순서가 FIFO 나이다."""
    try:
        raw = json.loads(_decode_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    keys = raw.get("decodable") if isinstance(raw, dict) else None
    if not isinstance(keys, list):
        return {}
    return dict.fromkeys(key for key in keys if isinstance(key, str))


def _load_decode_cache() -> dict[str, None]:
    global _decode_cache
    if _decode_cache is None:
        _decode_cache = _read_decode_cache_file()
    return _decode_cache


def _record_decode_cache(fresh: set[str]) -> None:
    """새 양성 판정을 영속화한다 — 실패는 조용히 무시(캐시는 최적화일 뿐)."""
    global _decode_cache
    cache = _load_decode_cache()
    added = sorted(key for key in fresh if key not in cache)
    if not added:
        return
    # 다른 프로세스가 그 사이 추가한 항목과 합친다. 마지막 쓰기가 이기지만
    # 잃는 것은 재검사 한 번이지 정확성이 아니다. 디스크 순서를 앞에 두어
    # 먼저 기록된 항목이 계속 더 오래된 쪽에 남게 한다.
    merged = dict.fromkeys([*_read_decode_cache_file(), *cache, *added])
    overflow = len(merged) - _DECODE_CACHE_MAX_ENTRIES
    if overflow > 0:
        merged = dict.fromkeys(list(merged)[overflow:])
    # 다음 호출이 방금 버린 항목을 다시 밀어 넣지 않게 메모리도 맞춘다.
    _decode_cache = merged
    try:
        _decode_cache_path().parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(
            _decode_cache_path(),
            json.dumps(
                {"version": 1, "decodable": list(merged)},
                ensure_ascii=False,
            ),
        )
    except OSError:
        pass


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
    """Return regular local images that each decode to at least one frame.

    저장된 판정은 **양성만** 재사용한다. 디코드 불가 판정은 실제 파손과
    일시적 도구 실패(ffmpeg 부재·타임아웃)를 구분하지 못하는데, 이를
    굳히면 멀쩡한 증거가 영구히 미지원으로 남는다 — 음성은 매 실행 재검사한다.
    """
    signatures = tuple(
        signature
        for path in dict.fromkeys(paths)
        if (signature := _image_signature(path)) is not None
    )
    signatures_by_path = {signature.path: signature for signature in signatures}
    remembered = _load_decode_cache()
    decoded: set[Path] = set()
    pending: list[_ImageSignature] = []
    for signature in signatures:
        if _signature_key(signature) in remembered:
            decoded.add(signature.path)
        else:
            pending.append(signature)
    for offset in range(0, len(pending), _DECODE_BATCH_SIZE):
        decoded.update(
            _probe_image_batch(
                tuple(pending[offset : offset + _DECODE_BATCH_SIZE])
            )
        )
    # 재검증(stat 재확인)을 통과한 것만 저장한다 — 검사 도중 바뀐 파일의
    # 판정이 디스크에 남지 않게.
    confirmed = frozenset(
        path
        for path in decoded
        if _image_signature(path) == signatures_by_path[path]
    )
    _record_decode_cache(
        {_signature_key(signatures_by_path[path]) for path in confirmed}
    )
    return confirmed


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
