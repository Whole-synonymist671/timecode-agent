"""Decoded-frame cadence verification for revision-bound NLE handoff."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Final
from weakref import WeakKeyDictionary

from .fsio import write_text_atomic
from .proc import run
from .revision import (
    RevisionInputSignature,
    current_revision_bindings,
    revision_input_signature,
)
from .revision_types import RevisionBindings
from .workspace import Workspace

_DECODE_TIMEOUT_SECONDS: Final = 600.0
_VERIFIED_CFR_CACHE: WeakKeyDictionary[
    Workspace, RevisionInputSignature
] = WeakKeyDictionary()
# 성공 증명의 프로세스 간 영속 — 전체 프레임 열거는 장편에서 분 단위라
# (757초 AV1 = 90초 실측, 2026-07-27) 프로세스 내 캐시만으로는 EDL·XML·
# FCPXML·OTIO 인계가 매번 같은 스캔을 되풀이한다. 디코드 cadence를 정하는
# 입력은 소스 바이트와 타이밍 revision뿐이므로 그 둘로만 무효화한다 —
# 전사 revision(diarize)은 프레임 타이밍을 바꾸지 않는다.
_RECEIPT_FILENAME: Final = ".tca-decoded-cfr.json"
_RECEIPT_SCHEMA: Final = 1
_RECEIPT_IDENTITY_FIELDS: Final = ("source_content_hash", "timing_revision_id")


class DecodedTimingError(RuntimeError):
    pass


def _positive_fraction(value: object, *, field: str) -> Fraction:
    try:
        parsed = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise DecodedTimingError(
            f"invalid decoded timing field: {field}"
        ) from error
    if parsed <= 0:
        raise DecodedTimingError(
            f"invalid decoded timing field: {field}"
        )
    return parsed


def decoded_frames_follow_nominal_cadence(
    frames: Sequence[tuple[int, int]],
    *,
    frame_rate: Fraction,
    time_base: Fraction,
) -> bool:
    """Prove every decoded frame start and end share one rational lattice."""
    if len(frames) < 2 or frame_rate <= 0 or time_base <= 0:
        return False
    step = Fraction(1, 1) / frame_rate / time_base
    if step < 1:
        return False
    origin = frames[0][0]
    phase_lower = Fraction(0, 1)
    phase_upper = Fraction(1, 1)
    previous = origin - 1
    previous_end: int | None = None

    def constrain_phase(timestamp: int, boundary_index: int) -> bool:
        nonlocal phase_lower, phase_upper
        offset = (
            Fraction(timestamp - origin, 1) - boundary_index * step
        )
        phase_lower = max(phase_lower, offset)
        phase_upper = min(phase_upper, offset + 1)
        return phase_lower < phase_upper

    for index, (timestamp, duration) in enumerate(frames):
        if (
            isinstance(timestamp, bool)
            or isinstance(duration, bool)
            or timestamp <= previous
            or duration <= 0
            or (previous_end is not None and timestamp != previous_end)
        ):
            return False
        previous = timestamp
        previous_end = timestamp + duration
        if not constrain_phase(timestamp, index):
            return False
        if not constrain_phase(previous_end, index + 1):
            return False
    return True


def _decoded_frames(
    video: Path, stream_index: int
) -> tuple[tuple[int, int], ...]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        str(stream_index),
        "-show_frames",
        "-show_entries",
        "frame=best_effort_timestamp,duration,pkt_duration",
        "-of",
        "json",
        str(video),
    ]
    completed = run(
        command,
        capture_output=True,
        text=True,
        timeout=_DECODE_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise DecodedTimingError(
            "decoded-frame probe failed: "
            f"{str(completed.stderr).strip() or completed.returncode}"
        )
    try:
        payload = json.loads(str(completed.stdout))
    except json.JSONDecodeError as error:
        raise DecodedTimingError(
            "decoded-frame probe returned invalid JSON"
        ) from error
    frames = payload.get("frames") if isinstance(payload, dict) else None
    if not isinstance(frames, list) or not frames:
        raise DecodedTimingError("decoded-frame probe returned no timestamps")
    decoded: list[tuple[int, int]] = []
    for frame in frames:
        if not isinstance(frame, dict):
            raise DecodedTimingError(
                "decoded-frame probe returned a missing timestamp"
            )
        value = frame.get("best_effort_timestamp")
        if value is None or isinstance(value, bool):
            raise DecodedTimingError(
                "decoded-frame probe returned a missing timestamp"
            )
        duration_value = frame.get("duration", frame.get("pkt_duration"))
        if duration_value is None or isinstance(duration_value, bool):
            raise DecodedTimingError(
                "decoded-frame probe returned a missing duration"
            )
        try:
            decoded.append((int(value), int(duration_value)))
        except (TypeError, ValueError) as error:
            raise DecodedTimingError(
                "decoded-frame probe returned invalid timing"
            ) from error
    return tuple(decoded)


def _stream_index(timing: Mapping[str, object]) -> int:
    stream_id = timing.get("stream_id")
    if not isinstance(stream_id, str):
        raise DecodedTimingError("decoded timing stream identity is missing")
    kind, separator, raw_index = stream_id.partition(":")
    if kind != "video" or separator != ":":
        raise DecodedTimingError("decoded timing stream identity is invalid")
    try:
        index = int(raw_index)
    except ValueError as error:
        raise DecodedTimingError(
            "decoded timing stream identity is invalid"
        ) from error
    if index < 0:
        raise DecodedTimingError("decoded timing stream identity is invalid")
    return index


def decoded_cfr_receipt_path(ws: Workspace) -> Path:
    """Sidecar holding one proven decoded-CFR verdict; safe to delete."""
    return ws.root / _RECEIPT_FILENAME


def _receipt_identity(
    ws: Workspace, bindings: RevisionBindings
) -> dict[str, str] | None:
    source_content_hash = ws.manifest.get("source_content_hash")
    if not isinstance(source_content_hash, str):
        return None
    return {
        "source_content_hash": source_content_hash,
        "source_revision_id": bindings["source_revision_id"],
        "timing_revision_id": bindings["timing_revision_id"],
    }


def _receipt_matches(ws: Workspace, identity: Mapping[str, str]) -> bool:
    try:
        payload = json.loads(
            decoded_cfr_receipt_path(ws).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("schema") != _RECEIPT_SCHEMA or payload.get("verified") is not True:
        return False
    return all(
        payload.get(field) == identity[field]
        for field in _RECEIPT_IDENTITY_FIELDS
    )


def _store_receipt(
    ws: Workspace, identity: Mapping[str, str], frame_count: int
) -> None:
    payload = {
        "schema": _RECEIPT_SCHEMA,
        "verified": True,
        "frame_count": frame_count,
        **identity,
    }
    try:
        write_text_atomic(
            decoded_cfr_receipt_path(ws),
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
    except OSError:
        # 증명은 이미 성립했다 — 캐시 기록 실패는 다음 실행의 재검증 비용일 뿐.
        pass


def _decoded_length_matches_stream(
    decoded: Sequence[tuple[int, int]],
    *,
    manifest: Mapping[str, object],
    timing: Mapping[str, object],
    frame_rate: Fraction,
) -> bool:
    """Compare the decoded timeline against the video stream, not the container.

    `manifest["duration"]` is the container maximum across all streams, so a
    longer audio track makes it describe the audio. Using it rejected genuinely
    constant-rate video (2026-07-27 실측).
    """
    duration_ts = timing.get("duration_ts")
    if isinstance(duration_ts, int) and not isinstance(duration_ts, bool):
        span = (decoded[-1][0] + decoded[-1][1]) - decoded[0][0]
        return span == duration_ts
    stream_duration = timing.get("stream_duration")
    if isinstance(stream_duration, (int, float)) and not isinstance(
        stream_duration, bool
    ):
        try:
            expected = _positive_fraction(
                stream_duration, field="stream_duration"
            )
        except DecodedTimingError:
            return False
        return round(expected * frame_rate) == len(decoded)
    if manifest.get("has_audio") is False:
        # 오디오가 없으면 컨테이너 duration이 곧 비디오 스트림의 길이다.
        try:
            expected = _positive_fraction(
                manifest.get("duration"), field="duration"
            )
        except DecodedTimingError:
            return False
        return round(expected * frame_rate) == len(decoded)
    # 스트림 타이밍이 없는 구 워크스페이스: 남은 근거는 start_pts·nb_frames와
    # decoded 격자 자체다. 컨테이너 길이로 대신 재면 오차단이 돌아온다.
    return True


def decoded_cfr_verified(ws: Workspace) -> bool:
    """Verify the complete decoded timeline, caching only an unchanged success."""
    bindings_before = current_revision_bindings(ws)
    if bindings_before is None:
        raise DecodedTimingError(
            "decoded CFR verification requires a revision-bound workspace"
        )
    signature_before = revision_input_signature(ws)
    if _VERIFIED_CFR_CACHE.get(ws) == signature_before:
        return True
    identity = _receipt_identity(ws, bindings_before)
    if identity is not None and _receipt_matches(ws, identity):
        _VERIFIED_CFR_CACHE[ws] = signature_before
        return True
    manifest = ws.manifest
    timing = manifest.get("timing")
    if not isinstance(timing, dict):
        return False
    try:
        frame_rate = _positive_fraction(
            timing.get("avg_frame_rate"),
            field="avg_frame_rate",
        )
        time_base = _positive_fraction(
            timing.get("time_base"),
            field="time_base",
        )
        stream_index = _stream_index(timing)
    except DecodedTimingError:
        return False
    decoded = _decoded_frames(ws.video, stream_index)
    start_pts = timing.get("start_pts")
    if (
        isinstance(start_pts, bool)
        or not isinstance(start_pts, int)
        or decoded[0][0] != start_pts
    ):
        return False
    nb_frames = timing.get("nb_frames")
    if (
        nb_frames is not None
        and (
            isinstance(nb_frames, bool)
            or not isinstance(nb_frames, int)
            or nb_frames != len(decoded)
        )
    ):
        return False
    if not _decoded_length_matches_stream(
        decoded,
        manifest=manifest,
        timing=timing,
        frame_rate=frame_rate,
    ):
        return False
    verified = decoded_frames_follow_nominal_cadence(
        decoded,
        frame_rate=frame_rate,
        time_base=time_base,
    )
    signature_after = revision_input_signature(ws)
    bindings_after = current_revision_bindings(ws)
    if (
        signature_after != signature_before
        or bindings_after != bindings_before
    ):
        raise DecodedTimingError(
            "workspace revision changed during decoded-frame verification"
        )
    if verified:
        _VERIFIED_CFR_CACHE[ws] = signature_after
        if identity is not None:
            _store_receipt(ws, identity, len(decoded))
    return verified
