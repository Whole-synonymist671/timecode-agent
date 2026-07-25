"""Editing handoff artifacts: EDL, xmeml, FCPXML, OTIO, SRT, markdown."""

from __future__ import annotations

import hashlib
from typing import Final

from .checkpoint_selection import selected_checkpoints as _selected
from .corpus_projection import narrative_head as narrative_head
from .corpus_projection import tagfmt as tagfmt
from .corpus_projection import ws_meta as ws_meta
from .image_index import capture_reasons as capture_reasons
from .scene_log import export_md as export_md
from .sequence_grounding import checkpoint_revision_hash
from .transcript_segments import load_transcript_segments
from .workspace import Workspace

# 포맷별 인계 능력 — 어떤 포맷이 Receipt 증명·시퀀스 러프컷을 감당하는지는
# CLI 어댑터가 아니라 포맷 정의 옆에서 산다. srt는 ids를 무시한 전사 렌더,
# md는 사람 표면, xml(xmeml)은 마커 전용이라 컷 순서·트림이 전달되지 않는다.
FORMAT_CAPS: Final[dict[str, frozenset[str]]] = {
    "edl": frozenset({"receipt", "sequence"}),
    "otio": frozenset({"receipt", "sequence"}),
    "fcpxml": frozenset({"receipt", "sequence"}),
    "xml": frozenset(),
    "md": frozenset(),
    "srt": frozenset(),
}


def validate_export_request(
    fmt: str, *,
    ids: list[str] | None = None,
    sequence_id: str | None = None,
    receipt: bool = False,
    output: str | None = None,
) -> None:
    """인계 옵션 조합의 정책 판정 — 위반이면 ValueError.

    argparse 경유 없이 성립해야 테스트가 라이브러리 레벨에서 닫힌다
    (어댑터에 정책이 살면 거절 분기가 무테스트로 남는다 — 2026-07-25 감사).
    """
    caps = FORMAT_CAPS.get(fmt, frozenset())
    if receipt and not output:
        raise ValueError(
            "--receipt는 -o/--output과 함께만 사용합니다 "
            "(산출물 파일 기준으로 해시를 고정)")
    if receipt and "receipt" not in caps:
        # 체크포인트 근거 증명이 성립하는 인계 포맷에서만 Receipt.
        raise ValueError(
            "--receipt는 edl/otio/fcpxml 인계 포맷에서만 지원합니다")
    if sequence_id and ids:
        raise ValueError("--ids와 --sequence는 동시에 쓸 수 없습니다")
    if sequence_id and "sequence" not in caps:
        raise ValueError(
            "--sequence는 edl/otio/fcpxml에서만 지원합니다"
            " (xml은 마커 전용 — 러프컷 인계 불가)")


def build_receipt(
    text: str, *, ws: Workspace, fmt: str,
    selection: list[dict] | None = None,
    checkpoints: list[dict] | None = None,
) -> dict:
    """인계 산출물 사이드카 Receipt — 무결성+근거 정체의 자립 증명.

    kinocut "Video Receipt"(편집 실행 원장) 개념의 이해-인계 치환: 받는
    쪽(NLE·후공정)이 원장 없이 ① 파일 비변조(artifact_sha256) ② 근거
    판정의 리비전·상태를 검증한다. 결정적 — 타임스탬프 없음(동일 입력=
    동일 Receipt, frozen replay 문화와 정합).
    """
    receipt: dict = {
        "schema": "tca-receipt-v1",
        "format": fmt,
        "artifact_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "workspace": ws.root.name,
        "video": ws.video.name,
    }
    if selection is not None:
        # 시퀀스 인계: 컷이 실은 핀(승격 시점 내용 증명)·상태를 그대로 승계.
        checkpoint_ids: list[str] = []
        revisions: dict = {}
        states: dict = {}
        sequence_id = None
        for c in selection:
            sequence_id = c.get("sequence_id") or sequence_id
            for checkpoint_id in c.get("checkpoint_ids") or []:
                if checkpoint_id not in checkpoint_ids:
                    checkpoint_ids.append(checkpoint_id)
            revisions.update(c.get("checkpoint_revisions") or {})
            states.update(c.get("checkpoint_states") or {})
        if sequence_id:
            receipt["sequence_id"] = sequence_id
        receipt["checkpoint_ids"] = checkpoint_ids
        receipt["checkpoint_revisions"] = revisions
        receipt["checkpoint_states"] = states
        return receipt
    # 직접 인계: 내보내기 시점 원장의 리비전 해시를 즉석 고정한다.
    # checkpoints가 주어지면 재독 없이 그 스냅샷을 증명한다 — 렌더와
    # Receipt 사이 원장 갱신이 끼어들어도 인증 대상이 갈라지지 않는다.
    # (ids 선택자는 제거 — 프로덕션 호출부는 항상 스냅샷을 넘긴다.
    #  해석은 checkpoint_selection.selected_checkpoints 소유.)
    if checkpoints is None:
        checkpoints = _selected(ws, None)
    receipt["checkpoint_ids"] = [str(c.get("id")) for c in checkpoints]
    receipt["checkpoint_revisions"] = {
        str(c.get("id")): checkpoint_revision_hash(c) for c in checkpoints
    }
    receipt["checkpoint_states"] = {
        str(c.get("id")): {
            "status": c.get("status"),
            "confidence": c.get("confidence"),
        }
        for c in checkpoints
    }
    return receipt


def _timecode(t: float, fps: int) -> str:
    frames_total = round(t * fps)
    ff = frames_total % fps
    ss = frames_total // fps
    hh, ss = divmod(ss, 3600)
    mm, ss = divmod(ss, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def _single_line_ref(value: object) -> str:
    """Keep untrusted grounding labels inside one handoff record."""
    text = "".join(
        char
        if (
            ord(char) in (0x09, 0x0A, 0x0D)
            or 0x20 <= ord(char) <= 0xD7FF
            or 0xE000 <= ord(char) <= 0xFFFD
            or 0x10000 <= ord(char) <= 0x10FFFF
        )
        else "\uFFFD"
        for char in str(value)
    )
    return " ".join(text.splitlines()).replace("* COMMENT:", "COMMENT:")


def _short_revision(pin: object) -> str:
    # 핀 값도 checkpoint id와 같은 미신뢰 원장 문자열이다 — 재적재는
    # 해시 형식을 재검증하지 않으므로 인계 직전에 동일 새니타이즈.
    return _single_line_ref(str(pin).split(":", 1)[-1][:12])


def _grounding(c: dict) -> list[str]:
    pins = c.get("checkpoint_revisions") or {}
    refs = []
    for checkpoint_id in c.get("checkpoint_ids") or []:
        label = _single_line_ref(checkpoint_id)
        if checkpoint_id in pins:
            label = f"{label}@{_short_revision(pins[checkpoint_id])}"
        refs.append(f"checkpoint:{label}")
    refs += [
        f"signal:{_single_line_ref(signal)}"
        for signal in (c.get("signals") or [])
    ]
    return refs


def _handoff_note(c: dict) -> str:
    note = str(c.get("situation") or c["hypothesis"]).splitlines()[0]
    grounding = _grounding(c)
    return (
        f"{note} | grounding: {', '.join(grounding)}"
        if grounding
        else note
    )


def export_edl(
    ws: Workspace, ids: list[str] | None = None,
    *, selection: list[dict] | None = None,
) -> str:
    m = ws.manifest
    fps = max(1, round(m["fps"]))
    clip_name = ws.video.name
    selected = selection if selection is not None else _selected(ws, ids)
    if len(selected) > 999:
        # CMX3600 hard cap — Premiere rejects the whole file past event 999.
        raise ValueError(
            f"EDL은 이벤트 999개가 하드캡인데 {len(selected)}개입니다 — "
            "--format xml(마커 무제한) 사용 또는 --ids로 분할하세요"
        )
    lines = [f"TITLE: {ws.video.stem}", "FCM: NON-DROP FRAME", ""]
    record = 0.0
    for i, c in enumerate(selected, 1):
        s, e = c["span"]
        dur = e - s
        lines.append(
            f"{i:03d}  AX       V     C        "
            f"{_timecode(s, fps)} {_timecode(e, fps)} "
            f"{_timecode(record, fps)} {_timecode(record + dur, fps)}"
        )
        lines.append(f"* FROM CLIP NAME: {clip_name}")
        lines.append(f"* COMMENT: {_handoff_note(c)}")
        lines.append("")
        record += dur
    return "\n".join(lines)


def export_xml(ws: Workspace, ids: list[str] | None = None) -> str:
    """FCP7 XML (xmeml v4) — duration markers per checkpoint.

    Premiere: File > Import 로 시퀀스+마커가 그대로 들어간다 (Resolve도 호환).
    """
    from xml.sax.saxutils import escape

    m = ws.manifest
    fps = max(1, round(m["fps"]))
    total = round(m["duration"] * fps)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE xmeml>",
        '<xmeml version="4">',
        " <sequence>",
        f"  <name>{escape(ws.video.stem)} (video-agent markers)</name>",
        f"  <duration>{total}</duration>",
        f"  <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>",
    ]
    for c in _selected(ws, ids):
        s, e = c["span"]
        comment = escape((c.get("situation") or c["hypothesis"]).strip())
        lines += [
            "  <marker>",
            f"   <name>{escape(c['id'])} [{c['status']}]</name>",
            f"   <comment>{comment}</comment>",
            f"   <in>{round(s * fps)}</in>",
            f"   <out>{round(e * fps)}</out>",
            "  </marker>",
        ]
    lines += [" </sequence>", "</xmeml>"]
    return "\n".join(lines) + "\n"


def _srt_ts(t: float) -> str:
    ms = round(t * 1000)
    s, ms = divmod(ms, 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def export_srt(ws: Workspace, ids: list[str] | None = None) -> str:
    """SRT 자막 — CapCut의 유일한 공식 반입 통로이자 전 편집기 공통 포맷.

    전사가 비면 OCR 의사 전사(자막 주도 영상)로 폴백한다. ids는 다른
    포맷과의 호출 시그니처 호환용으로만 존재한다(자막은 전 구간).
    """
    del ids
    source = [
        segment
        for segment in load_transcript_segments(ws.transcript_path)
        if segment["text"].strip()
    ]
    if not source:
        source = [
            segment
            for segment in load_transcript_segments(
                ws.root / "ocr_transcript.json"
            )
            if segment["text"].strip()
        ]
    if not source:
        raise ValueError(
            "자막으로 내보낼 전사·OCR 텍스트가 없습니다 — "
            "va ingest(전사) 또는 va ocr --every(스캔) 후 재시도"
        )
    lines: list[str] = []
    for i, s in enumerate(source, 1):
        lines += [str(i),
                  f"{_srt_ts(float(s['start']))} --> "
                  f"{_srt_ts(float(s['end']))}",
                  str(s["text"]).strip(), ""]
    return "\n".join(lines)


# NTSC 계열은 1001 분수 정확값 — 반올림 정수 fps는 장편에서 프레임 드리프트
_NTSC_RATES = ((23.976, (1001, 24000)), (29.97, (1001, 30000)),
               (59.94, (1001, 60000)))


def _frame_duration(fps: float) -> tuple[int, int]:
    """FCPXML frameDuration 유리수 (분자, 분모) — 초당 프레임의 역수."""
    for rate, frac in _NTSC_RATES:
        if abs(fps - rate) < 0.005:
            return frac
    return (1, max(1, round(fps)))


def _fcp_time(seconds: float, num: int, den: int) -> str:
    """FCPXML 시간값 — frameDuration 정수배(프레임 정렬)가 규약이다."""
    frames = round(seconds * den / num)
    return f"{frames * num}/{den}s"


def export_fcpxml(
    ws: Workspace, ids: list[str] | None = None,
    *, selection: list[dict] | None = None,
) -> str:
    """FCPXML 1.11 — Final Cut Pro 직접 반입(Resolve 수입도 지원).

    선택 체크포인트를 spine 위 asset-clip 러프컷으로 순서 배치하고,
    클립마다 근거 마커(id·상태·상황)를 싣는다. FCP7 xmeml(마커 전용)과
    달리 편집 결정(컷 배치)까지 전달한다.
    """
    from xml.sax.saxutils import quoteattr

    m = ws.manifest
    num, den = _frame_duration(float(m["fps"]))

    def t(seconds: float) -> str:
        return _fcp_time(seconds, num, den)

    size_attrs = ""
    if m.get("width") and m.get("height"):
        size_attrs = f' width="{int(m["width"])}" height="{int(m["height"])}"'
    has_audio = "1" if m.get("has_audio") else "0"
    selected = selection if selection is not None else _selected(ws, ids)
    clips: list[str] = []
    offset = 0.0
    for c in selected:
        s, e = c["span"]
        note = _handoff_note(c)
        clips += [
            f'    <asset-clip ref="r2" offset="{t(offset)}" '
            f'start="{t(s)}" duration="{t(e - s)}" '
            f'name={quoteattr(c["id"])} format="r1">',
            f'     <marker start="{t(s)}" duration="{t(e - s)}" '
            f'value={quoteattr(f"{c['id']} [{c['status']}]")} '
            f'note={quoteattr(note)}/>',
            "    </asset-clip>",
        ]
        offset += e - s
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE fcpxml>",
        '<fcpxml version="1.11">',
        " <resources>",
        f'  <format id="r1" frameDuration="{num}/{den}s"{size_attrs}/>',
        f'  <asset id="r2" name={quoteattr(ws.video.stem)} start="0s" '
        f'duration="{t(float(m["duration"]))}" hasVideo="1" '
        f'hasAudio="{has_audio}" format="r1">',
        "   <media-rep kind=\"original-media\" "
        f"src={quoteattr(ws.video.resolve().as_uri())}/>",
        "  </asset>",
        " </resources>",
        " <library>",
        '  <event name="video-agent">',
        f'   <project name={quoteattr(ws.video.stem + " (video-agent)")}>',
        f'    <sequence format="r1" duration="{t(offset)}" tcStart="0s">',
        "     <spine>",
        *clips,
        "     </spine>",
        "    </sequence>",
        "   </project>",
        "  </event>",
        " </library>",
        "</fcpxml>",
    ]
    return "\n".join(lines) + "\n"


def export_otio(
    ws: Workspace, ids: list[str] | None = None,
    *, selection: list[dict] | None = None,
) -> str:
    """OpenTimelineIO JSON — Premiere Pro 26.0+(2026-01 GA)·Resolve·ASWF 표준.

    체크포인트마다 소스 구간을 참조하는 클립 + 근거 메타데이터를 담은
    마커(video_agent 네임스페이스)를 싣는다. xmeml과 병행 제공.
    """
    try:
        import opentimelineio as otio
    except ImportError:
        raise RuntimeError(
            "OpenTimelineIO 백엔드 누락 — 기본 설치가 불완전합니다; "
            "같은 배포 소스에서 scripts/install.sh를 다시 실행하세요"
        ) from None

    m = ws.manifest
    fps = max(1, round(m["fps"]))

    def rt(seconds: float) -> "otio.opentime.RationalTime":
        return otio.opentime.RationalTime(round(seconds * fps), fps)

    timeline = otio.schema.Timeline(name=f"{ws.video.stem} (video-agent)")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    media = otio.schema.ExternalReference(
        target_url=str(ws.video),
        available_range=otio.opentime.TimeRange(rt(0), rt(m["duration"])),
    )
    for c in (selection if selection is not None else _selected(ws, ids)):
        s, e = c["span"]
        clip = otio.schema.Clip(
            name=c["id"],
            media_reference=media,
            source_range=otio.opentime.TimeRange(rt(s), rt(e - s)),
        )
        clip.markers.append(otio.schema.Marker(
            name=f"{c['id']} [{c['status']}]",
            marked_range=otio.opentime.TimeRange(rt(0), rt(e - s)),
            color=(otio.schema.MarkerColor.GREEN
                   if c["status"] in ("verified", "corrected")
                   else otio.schema.MarkerColor.YELLOW),
            metadata={"video_agent": {
                "status": c["status"],
                "confidence": c.get("confidence"),
                "hypothesis": c["hypothesis"],
                "situation": c.get("situation"),
                "evidence": c.get("evidence"),
                "sequence_id": c.get("sequence_id"),
                "order": c.get("order"),
                "checkpoint_ids": c.get("checkpoint_ids") or [],
                "checkpoint_revisions": dict(
                    c.get("checkpoint_revisions") or {}
                ),
                "checkpoint_states": {
                    checkpoint_id: dict(state)
                    for checkpoint_id, state in (
                        c.get("checkpoint_states") or {}
                    ).items()
                },
                "signals": c.get("signals") or [],
                "span_seconds": [s, e],
            }},
        ))
        track.append(clip)
    return otio.adapters.write_to_string(timeline, "otio_json")
