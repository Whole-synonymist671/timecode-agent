"""On-device screen-text extraction via macOS Vision framework.

Deterministic, ~200ms/frame, zero vision-LLM tokens — for nameplates,
killfeeds, donation overlays. The macOS runtime installs ocrmac/pyobjc by
default. Korean needs explicit ko-KR preference (Vision auto-detect is
unreliable for CJK).
"""

from __future__ import annotations

import json

from .capture import capture_frames
from .fsio import write_text_atomic
from .workspace import Workspace

DEFAULT_LANGS = ["ko-KR", "en-US"]


def ocr_frame(
    ws: Workspace,
    t: float,
    crop: str | None = None,
    langs: list[str] | None = None,
    reason: str = "ocr-frame",
) -> list[dict]:
    """Capture (cached) the frame at t, OCR it. [{text, confidence, bbox}]."""
    try:
        from ocrmac import ocrmac  # pyright: ignore[reportMissingImports]
    except ImportError:
        raise RuntimeError(
            "ocr backend unavailable — macOS installs ocrmac by default; "
            "rerun scripts/install.sh from the same release source"
        ) from None
    (frame,) = capture_frames(ws, [t], crop=crop, reason=reason)
    annotations = ocrmac.OCR(
        str(frame),
        language_preference=langs or DEFAULT_LANGS,
        recognition_level="accurate",
    ).recognize()
    return [
        {"text": text, "confidence": round(conf, 3),
         "bbox": [round(v, 4) for v in bbox]}
        for text, conf, bbox in annotations
    ]


def ocr_scan(
    ws: Workspace,
    every: float = 5.0,
    crop: str | None = None,
    langs: list[str] | None = None,
    start: float = 0.0,
    end: float | None = None,
    min_conf: float = 0.3,
) -> list[dict]:
    """Even-interval OCR sweep -> consecutive identical captions merged.

    Pseudo-transcript for caption-driven / speechless videos where whisper
    yields nothing (BGM story videos, killfeed periods). Saves
    ocr_transcript.json; frames go through the normal capture cache.
    """
    if every <= 0:
        raise ValueError("--every must be > 0 seconds")
    duration = ws.manifest["duration"]
    end = duration if end is None else min(end, duration)
    entries: list[dict] = []
    t = start
    # 스캔 소인을 출처 원장에 남긴다 — 대량 추출 프레임이 '의도적 검증
    # 캡처'와 구분되지 않던 원인-미상 축적(lvbench-pilot 865장)의 재발 방지
    scan_reason = f"ocr-scan every={every:g}s"
    while t < end:
        lines = [
            r for r in ocr_frame(ws, t, crop=crop, langs=langs,
                                 reason=scan_reason)
            if r["confidence"] >= min_conf
        ]
        # Vision bbox is bottom-left origin: larger y = higher on screen.
        lines.sort(key=lambda r: -r["bbox"][1])
        text = " ".join(" ".join(r["text"].split()) for r in lines).strip()
        if text and entries and entries[-1]["text"] == text:
            entries[-1]["end"] = round(min(t + every, end), 3)
        elif text:
            entries.append({
                "start": round(t, 3),
                "end": round(min(t + every, end), 3),
                "text": text,
            })
        t += every
    write_text_atomic(ws.root / "ocr_transcript.json",
                      json.dumps(entries, ensure_ascii=False, indent=1))
    import platform

    ws.stamp_tool("ocr", f"apple-vision(macos-{platform.mac_ver()[0]})")
    return entries
