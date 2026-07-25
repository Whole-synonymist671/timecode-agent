"""Targeted frame capture with snapshot caching (never sweep all frames)."""

from __future__ import annotations

import hashlib
import re
import tempfile
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from .image_provenance import (
    ImageEventInput,
    append_image_provenance,
    cause_identity,
    record_legacy_capture_reasons,
)
from .image_validation import is_decodable_image
from .proc import run
from .timestamps import frame_name
from .workspace import Workspace


def _reasons_per_timestamp(
    reason: str | Sequence[str | None] | None, count: int
) -> list[str | None]:
    """reason 1개=전체 적용, N개=타임스탬프별 페어링 — 그 외 개수는 거부."""
    if reason is None or isinstance(reason, str):
        return [reason] * count
    reasons = list(reason)
    if len(reasons) == 1:
        return reasons * count
    if len(reasons) != count:
        raise ValueError(
            f"--reason 개수({len(reasons)})는 1개(전체 적용) 또는 "
            f"-t 개수({count})와 같아야 합니다"
        )
    return reasons


def _record_capture_batch(
    ws: Workspace,
    paths: list[Path],
    timestamps: list[float],
    *,
    requested_timestamps: list[float],
    crop: str | None,
    reasons: list[str | None],
) -> None:
    distinct = set(reasons)
    capture_cause = cause_identity(ws, "capture", {
        "timestamps": requested_timestamps,
        "crop": crop,
        # 단일 사유는 기존 payload 형태 유지 — 과거 이벤트와의 cause 병합
        # 연속성을 지키고, 사유가 갈릴 때만 리스트로 확장한다.
        "reason": reasons[0] if len(distinct) <= 1 else reasons,
    })
    provenance: list[ImageEventInput] = [
        {
            "path": f"frames/{path.name}",
            "t": timestamp,
            "crop": crop,
            "reason": item_reason,
            "kind": "frame-crop" if crop else "frame",
            "cause_type": "capture",
            "cause_id": capture_cause,
        }
        for path, timestamp, item_reason in zip(paths, timestamps, reasons)
    ]
    append_image_provenance(ws, provenance)
    record_legacy_capture_reasons(ws, provenance)


def capture_frames(
    ws: Workspace, ts: list[float],
    crop: str | None = None,
    reason: str | Sequence[str | None] | None = None,
) -> list[Path]:
    """Capture one frame per timestamp; cached per (timestamp, crop).

    crop is an ffmpeg crop expression "w:h:x:y" (iw/ih allowed) — lets the
    agent read just a UI region (killfeed, alert box) as a tiny image, with
    Claude vision doing the OCR. No OCR engine dependency.

    reason (optional) records which signal triggered this capture
    (e.g. "burst-97s", "scene-change", "endcard", "gap-verify") into the
    append-only causal ledger so the workspace stays auditable across
    sessions. 문자열 1개면 전체 프레임에, 시퀀스면 -t와 같은 개수로
    타임스탬프별 페어링된다.
    """
    duration = ws.manifest["duration"]
    video = ws.video
    rounded_ts = [round(t, 3) for t in ts]
    reasons = _reasons_per_timestamp(reason, len(ts))
    for t in ts:
        if not 0 <= t <= duration + 0.5:
            raise ValueError(
                f"timestamp {t}s out of range (video duration {duration:.2f}s)"
            )
    suffix = ""
    if crop:
        if any(c in crop for c in ";'\"[]"):
            raise ValueError(f"invalid crop expression: {crop!r}")
        suffix = "_c" + hashlib.md5(crop.encode()).hexdigest()[:8]
    out: list[Path] = []
    try:
        for t, item_reason in zip(ts, reasons):
            slug = _reason_slug(item_reason)
            name = frame_name(t)
            if suffix:
                name = name.replace(".jpg", f"{suffix}.jpg")
            base = name[:-4]
            dest, cache_hit = _resolve_frame_dest(ws.frames_dir, base, slug)
            if not cache_hit:
                _capture_one(ws.frames_dir, video, t, crop, dest)
            out.append(dest)
    except Exception:
        _record_capture_batch(
            ws, out, rounded_ts[:len(out)], requested_timestamps=rounded_ts,
            crop=crop, reasons=reasons[:len(out)],
        )
        raise
    _record_capture_batch(
        ws, out, rounded_ts, requested_timestamps=rounded_ts,
        crop=crop, reasons=reasons,
    )
    return out


def _capture_one(
    frames_dir: Path, video: Path, t: float, crop: str | None, dest: Path
) -> None:
    """한 프레임을 임시 파일로 추출해 검증 후 원자 교체한다."""
    with tempfile.NamedTemporaryFile(
        prefix=f".{dest.stem}.", suffix=dest.suffix,
        dir=frames_dir, delete=False,
    ) as temporary_file:
        temporary = Path(temporary_file.name)
    try:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{t:.3f}", "-i", str(video),
            "-frames:v", "1", "-q:v", "3",
        ]
        if crop:
            cmd += ["-vf", f"crop={crop}"]
        cmd.append(str(temporary))
        result = run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not is_decodable_image(temporary):
            raise RuntimeError(
                f"ffmpeg capture failed at {t}s "
                f"({result.returncode}): {result.stderr.strip()}"
            )
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)


_SLUG_RE = re.compile(r"[^0-9A-Za-z가-힣-]+")


def _reason_slug(reason: str | None) -> str:
    """캡처 사유를 파일명 접미로 — 생성 시점 라벨링(브레드크럼 가독성).

    t{ms} 접두는 캐시 키로 불변이고, 소인은 그 뒤에 붙는 표시 계층이다.
    """
    if not reason:
        return ""
    slug = _SLUG_RE.sub("-", unicodedata.normalize("NFC", reason)).strip("-")
    return f"-{slug[:40].rstrip('-')}" if slug else ""


def _resolve_frame_dest(frames_dir: Path, base: str, slug: str) -> tuple[Path, bool]:
    """같은 (t, crop) 캐시 조회 — 정확 키 뒤의 소인 유무·차이만 무시.

    (경로, 캐시히트) 반환. 손상 캐시가 있으면 그 이름을 재사용해 덮어쓴다.
    """
    candidates = sorted((
        *frames_dir.glob(f"{base}.jpg"),
        *frames_dir.glob(f"{base}-*.jpg"),
    ))
    for candidate in candidates:
        if is_decodable_image(candidate):
            return candidate, True
    if candidates:
        return candidates[0], False
    return frames_dir / f"{base}{slug}.jpg", False


_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def sharpness(frame: Path) -> float:
    """Edge-energy sharpness proxy: sobel 필터 후 평균 luma (0~255).

    모션블러·포커스아웃 프레임은 에지가 뭉개져 점수가 낮다. ffmpeg만 사용
    (외부 의존 0), 같은 입력이면 같은 점수.
    """
    res = run([
        "ffmpeg", "-loglevel", "error", "-i", str(frame),
        "-vf", "sobel,signalstats,metadata=print:file=-",
        "-f", "null", "-",
    ], capture_output=True, text=True)
    m = _YAVG_RE.search(res.stdout or "")
    if res.returncode != 0 or not m:
        raise RuntimeError(f"sharpness probe failed for {frame}: {res.stderr.strip()}")
    return float(m.group(1))


def capture_sharpest(
    ws: Workspace, ts: list[float], *, window: float = 0.3,
    reason: str | Sequence[str | None] | None = None,
) -> list[Path]:
    """t±window 3후보 중 선명도 최고 프레임을 채택한다 (future-queue #26).

    산업 파이프라인(SVD·MovieGen·HunyuanVideo·Koala-36M VTSS)이 blur를
    결정적으로 필터링하는 패턴의 판독-측 치환 — 미세 디테일 판독 전
    모션블러 프레임을 자동 회피한다. 동점은 요청 시점에 가까운 쪽,
    그다음 이른 쪽(결정적). 후보 프레임은 캐시에 남는다(재확인 비용 0).
    """
    duration = float(ws.manifest["duration"])
    reasons = _reasons_per_timestamp(reason, len(ts))
    candidate_groups: list[tuple[float, str | None, list[float]]] = []
    for t, item_reason in zip(ts, reasons):
        cands = sorted({
            round(max(0.0, t - window), 3),
            round(t, 3),
            round(min(duration, t + window), 3),
        })
        candidate_groups.append((t, item_reason, cands))
    unique_times = sorted(
        {t for _, _, cands in candidate_groups for t in cands}
    )
    unique_frames = capture_frames(ws, unique_times)
    frame_at = dict(zip(unique_times, unique_frames))

    out: list[Path] = []
    records: list[ImageEventInput] = []
    legacy_winners: list[ImageEventInput] = []
    for t, item_reason, cands in candidate_groups:
        frames = [frame_at[candidate_t] for candidate_t in cands]
        scored = [
            (sharpness(p), -abs(ct - t), -ct, p, ct)
            for p, ct in zip(frames, cands)
        ]
        best = max(scored)
        out.append(best[3])
        selection_reason = (
            f"sharp-gate({item_reason})" if item_reason else "sharp-gate"
        )
        selection_context = {
            "requested_t": round(t, 3),
            "window": round(window, 3),
            "reason": selection_reason,
        }
        selection_cause = cause_identity(
            ws, "sharpness-selection", selection_context
        )
        scores = {f"{ct}": round(s, 2) for s, _, _, _p, ct in scored}
        for _score, _near, _early, frame, candidate_t in scored:
            event: ImageEventInput = {
                "path": f"frames/{frame.name}",
                "t": candidate_t,
                "crop": None,
                "reason": selection_reason,
                "kind": "frame",
                "role": ("sharpness-selected"
                         if frame == best[3] else "sharpness-candidate"),
                "cause_type": "sharpness-selection",
                "cause_id": selection_cause,
                "requested_t": round(t, 3),
                "window": round(window, 3),
                "scores": scores,
            }
            records.append(event)
            if frame == best[3]:
                legacy_winners.append(event)
    append_image_provenance(ws, records)
    record_legacy_capture_reasons(ws, legacy_winners)
    return out
