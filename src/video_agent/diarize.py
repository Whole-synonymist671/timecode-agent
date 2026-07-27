"""Speaker diarization via bundled pyannote 3.1 and sherpa fallback.

Gated model: needs a HuggingFace token AND accepting conditions at
hf.co/pyannote/speaker-diarization-3.1 + hf.co/pyannote/segmentation-3.0.
Visual speaker labels from the loop stay primary; diarization adds
who-spoke-when structure the agent maps onto them.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Protocol, runtime_checkable

from .audio_cache import cached_audio_wav
from .cache_paths import cache_root
from .diarization_transaction import (
    begin_diarization_transaction,
    complete_diarization_transaction,
    rollback_diarization_transaction_locked,
)
from .fsio import write_text_atomic
from .ingest import Segment
from .probe import source_stat_fingerprint
from .revision import (
    RevisionBindingError,
    assert_transcript_revision_update_allowed,
    current_revision_bindings,
    publish_workspace_revisions,
    revision_input_signature,
)
from .transcript_segments import load_transcript_segments_for_update
from .workspace import Workspace
from .workspace_lock import stable_workspace_lock

_GATE_HELP = (
    "pyannote 모델 접근 실패 — 다음 3단계가 필요합니다:\n"
    "  1) hf.co/pyannote/speaker-diarization-3.1 에서 조건 동의\n"
    "  2) hf.co/pyannote/segmentation-3.0 에서 조건 동의\n"
    "  3) HF 토큰 설정: `hf auth login` 또는 HF_TOKEN 환경변수"
)


def assign_speakers(
    segments: list[Segment], turns: list[dict]
) -> list[dict]:
    """Per segment: speaker with max time-overlap (None if no overlap)."""
    out = []
    for seg in segments:
        best, best_ov = None, 0.0
        for t in turns:
            ov = min(seg.end, t["end"]) - max(seg.start, t["start"])
            if ov > best_ov:
                best, best_ov = t["speaker"], ov
        d = asdict(seg)
        d = {k: v for k, v in d.items() if v is not None}
        d["speaker"] = best
        out.append(d)
    return out


_SHERPA_MODELS = {
    "segmentation.onnx": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "speaker-segmentation-models/"
        "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"),
    "embedding.onnx": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "speaker-recongition-models/"
        "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"),
}


def _sherpa_model_dir() -> Path:
    d = cache_root() / "diarize"
    d.mkdir(parents=True, exist_ok=True)
    for name, url in _SHERPA_MODELS.items():
        if (d / name).is_file():
            continue
        print(f"downloading {name}…", file=sys.stderr)
        import io
        import tarfile
        import urllib.request

        data = urllib.request.urlopen(url, timeout=120).read()
        if url.endswith(".tar.bz2"):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tf:
                member = next(m for m in tf.getmembers()
                              if m.name.endswith("model.onnx"))
                extracted = tf.extractfile(member)
                if extracted is None:
                    raise RuntimeError("sherpa archive model is not a regular file")
                (d / name).write_bytes(extracted.read())
        else:
            (d / name).write_bytes(data)
    return d


class _TurnSpan(Protocol):
    start: float
    end: float


@runtime_checkable
class _Annotation(Protocol):
    def itertracks(
        self,
        *,
        yield_label: bool,
    ) -> Iterable[tuple[_TurnSpan, str | None, str]]: ...


@runtime_checkable
class _DiarizeOutput(Protocol):
    speaker_diarization: _Annotation


def _turns_from_pyannote_output(
    output: (
        _Annotation
        | _DiarizeOutput
        | Iterable[tuple[_Annotation, _Annotation]]
    ),
) -> list[dict]:
    """Normalize pyannote 3.x Annotation and 4.x DiarizeOutput."""
    if isinstance(output, _DiarizeOutput):
        annotation = output.speaker_diarization
    elif isinstance(output, _Annotation):
        annotation = output
    else:
        raise RuntimeError("pyannote가 예상하지 못한 반복형 결과를 반환했습니다")
    return [
        {
            "start": round(turn.start, 3),
            "end": round(turn.end, 3),
            "speaker": (
                f"S{label}"
                if not str(label).startswith(("S", "SPEAKER"))
                else str(label)
            ),
        }
        for turn, _, label in annotation.itertracks(yield_label=True)
    ]


def _pyannote_turns(wav: Path, num_speakers: int | None) -> list[dict]:
    try:
        from pyannote.audio import (  # pyright: ignore[reportMissingImports]
            Pipeline,
        )
    except ImportError:
        raise RuntimeError(
            "pyannote 백엔드 미설치 — diarize extra로 설치합니다: "
            "`uv tool install --python 3.12 '.[diarize]'` 또는 "
            "scripts/install.sh(기본 포함). 설치 없이도 `--backend sherpa`"
            "(게이트 없음)가 즉시 동작합니다"
        ) from None
    try:
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
        if pipeline is None:
            raise RuntimeError(_GATE_HELP)
    except Exception as e:  # gated 401/403 등
        raise RuntimeError(f"{_GATE_HELP}\n(원인: {e})") from None
    if num_speakers:
        return _turns_from_pyannote_output(
            pipeline(str(wav), num_speakers=num_speakers)
        )
    return _turns_from_pyannote_output(pipeline(str(wav)))


def _sherpa_turns(
    wav: Path, num_speakers: int | None, threshold: float = 0.9
) -> list[dict]:
    """Ungated fallback: sherpa-onnx pyannote-segmentation ONNX (Apache)."""
    try:
        import numpy as np
        import sherpa_onnx
    except ImportError:
        raise RuntimeError(
            "sherpa 백엔드 누락 — 기본 설치가 불완전합니다; "
            "같은 배포 소스에서 scripts/install.sh를 다시 실행하세요"
        ) from None
    import wave

    d = _sherpa_model_dir()
    clustering = (sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers)
                  if num_speakers
                  else sherpa_onnx.FastClusteringConfig(threshold=threshold))
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(d / "segmentation.onnx"))),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(d / "embedding.onnx")),
        clustering=clustering,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    with wave.open(str(wav)) as w:
        samples = (np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                   .astype(np.float32) / 32768)
    result = sd.process(samples).sort_by_start_time()
    return [{"start": round(r.start, 3), "end": round(r.end, 3),
             "speaker": f"S{r.speaker}"} for r in result]


def _cached_hf_token() -> str | None:
    """`hf auth login`이 남긴 캐시 토큰 — env 없이 로그인한 문서 경로."""
    try:
        from huggingface_hub import get_token
    except ImportError:      # hub 미설치면 캐시 경로 자체가 없다
        return None
    try:
        return get_token()
    except Exception:        # 캐시 판독 실패가 백엔드 선택을 죽이면 안 된다
        return None


def _auto_backend(environment: Mapping[str, str]) -> str:
    """Avoid a guaranteed gated network failure when no HF token exists."""
    if (
        environment.get("HF_TOKEN")
        or environment.get("HUGGING_FACE_HUB_TOKEN")
        or _cached_hf_token()
    ):
        return "pyannote"
    return "sherpa"


def compute_diarization(
    ws: Workspace,
    num_speakers: int | None = None,
    backend: str = "auto",
) -> list[dict]:
    """Compute and publish one transcript revision under an exclusive lease."""
    with stable_workspace_lock(
        ws.root,
        ws.workspace_lock_path,
        exclusive=True,
        allow_reentrant_exclusive=True,
    ):
        rollback_diarization_transaction_locked(ws.root)
        return _compute_diarization_locked(
            ws,
            num_speakers=num_speakers,
            backend=backend,
        )


def _compute_diarization_locked(
    ws: Workspace,
    num_speakers: int | None = None,
    backend: str = "auto",
) -> list[dict]:
    previous_revision = assert_transcript_revision_update_allowed(ws)
    if (
        previous_revision is None
        and ws.manifest.get("revision_draft") is not True
    ):
        raise RevisionBindingError(
            "legacy workspace is read-only for diarization; ingest into a "
            "fresh revision-bound workspace"
        )
    transcript = load_transcript_segments_for_update(ws.transcript_path)
    if previous_revision is None:
        previous_revision = publish_workspace_revisions(ws)
    revision_inputs_before = revision_input_signature(ws)
    source_stat_before = source_stat_fingerprint(ws.video.stat())
    transcript_before = ws.transcript_path.read_text(encoding="utf-8")
    manifest_before = ws.manifest_path.read_text(encoding="utf-8")
    diarization_path = ws.root / "diarization.json"
    diarization_before = (
        diarization_path.read_text(encoding="utf-8")
        if diarization_path.is_file()
        else None
    )
    segments = [
        Segment(
            id=(
                int(raw_id)
                if isinstance((raw_id := segment.get("id")), int)
                and not isinstance(raw_id, bool)
                else index
            ),
            start=float(segment["start"]),
            end=float(segment["end"]),
            text=segment["text"],
            logprob=(
                float(value)
                if isinstance((value := segment.get("logprob")), (int, float))
                and not isinstance(value, bool)
                else None
            ),
            no_speech_prob=(
                float(value)
                if isinstance(
                    (value := segment.get("no_speech_prob")),
                    (int, float),
                )
                and not isinstance(value, bool)
                else None
            ),
            conf=(
                float(value)
                if isinstance((value := segment.get("conf")), (int, float))
                and not isinstance(value, bool)
                else None
            ),
        )
        for index, segment in enumerate(transcript)
    ]
    # 워크스페이스 오디오 캐시 재사용 — ingest가 이미 뽑아 둔 wav가 있으면
    # 추출 없이 바로 쓴다(없으면 여기서 추출해 이후 audioevents가 재사용).
    wav = cached_audio_wav(ws)
    if backend == "pyannote":
        turns = _pyannote_turns(wav, num_speakers)
        used = "pyannote"
    elif backend == "sherpa":
        turns = _sherpa_turns(wav, num_speakers)
        used = "sherpa"
    else:
        selected = _auto_backend(os.environ)
        if selected == "sherpa":
            turns = _sherpa_turns(wav, num_speakers)
            used = "sherpa"
        else:
            try:
                turns = _pyannote_turns(wav, num_speakers)
                used = "pyannote"
            except RuntimeError as e:
                print(f"pyannote 불가 → sherpa 폴백\n({e})", file=sys.stderr)
                turns = _sherpa_turns(wav, num_speakers)
                used = "sherpa"
    merged = assign_speakers(segments, turns)
    revision_after_backend = assert_transcript_revision_update_allowed(ws)
    if revision_after_backend != previous_revision:
        raise RevisionBindingError(
            "workspace revision changed during diarization"
        )
    if revision_input_signature(ws) != revision_inputs_before:
        raise RevisionBindingError(
            "workspace revision source changed during diarization"
        )
    if current_revision_bindings(ws) != previous_revision:
        raise RevisionBindingError(
            "workspace revision changed during diarization"
        )
    begin_diarization_transaction(
        ws.root,
        manifest_text=manifest_before,
        transcript_text=transcript_before,
        diarization_text=diarization_before,
    )
    try:
        write_text_atomic(
            diarization_path,
            json.dumps(turns, ensure_ascii=False, indent=1),
        )
        write_text_atomic(
            ws.transcript_path,
            json.dumps(merged, ensure_ascii=False, indent=1),
        )
        manifest = ws.manifest
        tools = dict(manifest.get("tools") or {})
        tools["diarize"] = used
        publish_workspace_revisions(
            ws,
            manifest_updates={"tools": tools},
            expected_source_stat=source_stat_before,
        )
        complete_diarization_transaction(ws.root)
    except BaseException:
        rollback_diarization_transaction_locked(ws.root)
        raise
    print(f"speakers: {len({t['speaker'] for t in turns})}, "
          f"turns: {len(turns)} — transcript.json에 speaker 필드 병합",
          file=sys.stderr)
    return turns
