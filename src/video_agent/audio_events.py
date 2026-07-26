"""Semantic audio events (laughter/applause/…) via macOS Sound Analysis.

Fourth placement signal: on-device SNClassifySoundRequest (300+ classes,
language-neutral, no model download). The macOS runtime installs the required
PyObjC framework by default. Selection stays with the agent — these are
candidate spans, not verdicts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .audio_cache import cached_audio_wav
from .fsio import write_text_atomic
from .workspace import Workspace

DEFAULT_LABELS = {
    "laughter", "giggling", "applause", "clapping", "cheering", "crowd",
    "screaming", "shout", "children_shouting", "booing", "gasp",
}
Hit = tuple[float, str, float]  # (t, label, confidence)


def merge_hits(hits: list[Hit], gap: float = 2.0,
               window: float = 1.5) -> list[dict]:
    """Consecutive same-label hits (≤gap apart) -> one event span."""
    events: list[dict] = []
    for t, label, conf in sorted(hits):
        last = events[-1] if events else None
        if last and last["label"] == label and t - last["end"] <= gap:
            last["end"] = round(t + window, 2)
            last["peak_conf"] = max(last["peak_conf"], round(conf, 2))
        else:
            events.append({"start": round(t, 2), "end": round(t + window, 2),
                           "label": label, "peak_conf": round(conf, 2)})
    return events


def _analyze(wav: Path, labels: set[str], min_conf: float) -> list[Hit]:
    try:
        import objc  # pyright: ignore[reportMissingImports]
        import SoundAnalysis as SA  # pyright: ignore[reportMissingImports]
        from Foundation import (  # pyright: ignore[reportMissingImports]
            NSURL,  # pyright: ignore[reportAttributeAccessIssue]
            NSObject,  # pyright: ignore[reportAttributeAccessIssue]
        )
    except ImportError:
        raise RuntimeError(
            "Sound Analysis backend unavailable — macOS installs it by "
            "default; rerun scripts/install.sh from the same release source"
        ) from None

    hits: list[Hit] = []
    proto = objc.protocolNamed("SNResultsObserving")

    class _Observer(NSObject, protocols=[proto]):  # pyright: ignore[reportGeneralTypeIssues, reportCallIssue]
        def request_didProduceResult_(self, request, result):
            tr = result.timeRange()
            start = tr[0] if isinstance(tr, tuple) else tr.start
            val = start[0] if isinstance(start, tuple) else start.value
            ts = start[1] if isinstance(start, tuple) else start.timescale
            t = val / ts if ts else 0.0
            for c in result.classifications():
                ident, conf = str(c.identifier()), float(c.confidence())
                if conf >= min_conf and ident in labels:
                    hits.append((t, ident, conf))

        def request_didFailWithError_(self, request, error):
            print(f"warning: sound analysis failed: {error}", file=sys.stderr)

        def requestDidComplete_(self, request):
            pass

    url = NSURL.fileURLWithPath_(str(wav))
    analyzer_type = SA.SNAudioFileAnalyzer  # pyright: ignore[reportAttributeAccessIssue]
    analyzer, err = analyzer_type.alloc().initWithURL_error_(url, None)
    if analyzer is None:
        raise RuntimeError(f"SNAudioFileAnalyzer init failed: {err}")
    request_type = SA.SNClassifySoundRequest  # pyright: ignore[reportAttributeAccessIssue]
    classifier_id = SA.SNClassifierIdentifierVersion1  # pyright: ignore[reportAttributeAccessIssue]
    request, err = (request_type.alloc()
                    .initWithClassifierIdentifier_error_(
                        classifier_id, None))
    if request is None:
        raise RuntimeError(f"SNClassifySoundRequest init failed: {err}")
    observer = _Observer.alloc().init()
    ok = analyzer.addRequest_withObserver_error_(request, observer, None)
    if isinstance(ok, tuple):
        ok = ok[0]
    if not ok:
        raise RuntimeError("SoundAnalysis addRequest failed")
    analyzer.analyze()
    return hits


def compute_audio_events(
    ws: Workspace,
    min_conf: float = 0.6,
    labels: set[str] | None = None,
) -> list[dict]:
    classified = ws.manifest["has_audio"]
    if not classified:
        print("warning: no audio stream — no audio events", file=sys.stderr)
        events: list[dict] = []
    else:
        # 워크스페이스 오디오 캐시 재사용 — ingest/diarize가 뽑아 둔 wav를
        # 그대로 쓴다(없으면 여기서 추출해 캐시에 남긴다).
        hits = _analyze(
            cached_audio_wav(ws), labels or DEFAULT_LABELS, min_conf
        )
        events = merge_hits(hits)
    write_text_atomic(ws.root / "audio_events.json",
                      json.dumps(events, ensure_ascii=False, indent=1))
    if classified:
        # P1 지각 provenance — 이벤트 라벨은 학습 모델 산물이라 어떤
        # 분류기 세대가 만들었는지 없으면 재현·감사 불가(diarize와 동일
        # 계약). 산출물이 실제로 남은 뒤에만 스탬프 — 쓰기가 실패했는데
        # manifest가 부재 산출물의 provenance를 주장하면 안 된다.
        ws.stamp_tool("audioevents", "macos-sound-analysis:v1")
    return events
