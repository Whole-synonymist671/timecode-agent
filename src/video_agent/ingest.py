"""Ingest facade: probe + acquisition + transcription -> transcript.json/srt.

내부 계층(전수점검 2026-07-26 A-3 분해):
- `ingest_acquire` — yt-dlp 영상·자막 획득, 진행률 스트리밍, 자막 선택
- `ingest_transcribe` — whisper 전사 구현, 품질 가드(붕괴·오염), 재분절
이 모듈은 공개 진입점(`ingest`/`ingest_session`)·워크스페이스
오케스트레이션(`_ingest_locked`)·백엔드 선택(`_transcribe`)을 소유한다.
아래 재수출은 기존 import·몽키패치 표면(`ingest._transcribe_faster_whisper`
등)의 호환 계약이다 — 오케스트레이션과 `_transcribe`의 폴백이 이 모듈
전역을 통해 호출하므로 setattr 패치가 분해 전과 동일하게 작동한다.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Iterator

from .audio_cache import cached_audio_wav
from .fsio import write_text_atomic
from .ingest_acquire import _download_auto_subs as _download_auto_subs
from .ingest_acquire import _download_url as _download_url
from .ingest_acquire import _pick_subtitle as _pick_subtitle
from .ingest_acquire import _promote_subtitle as _promote_subtitle
from .ingest_acquire import _run_download as _run_download
from .ingest_acquire import _subtitle_sidecars as _subtitle_sidecars
from .ingest_transcribe import COLLAPSE_COVERAGE as COLLAPSE_COVERAGE
from .ingest_transcribe import COLLAPSE_MIN_SPEECH as COLLAPSE_MIN_SPEECH
from .ingest_transcribe import Segment as Segment
from .ingest_transcribe import _hotword_contaminated as _hotword_contaminated
from .ingest_transcribe import _spoken_seconds as _spoken_seconds
from .ingest_transcribe import (
    _transcribe_faster_whisper as _transcribe_faster_whisper,
)
from .ingest_transcribe import _transcript_collapsed as _transcript_collapsed
from .ingest_transcribe import _vad_speech_chunks as _vad_speech_chunks
from .ingest_transcribe import _vad_speech_seconds as _vad_speech_seconds
from .ingest_transcribe import _write_srt as _write_srt
from .ingest_transcribe import resegment_by_words as resegment_by_words
from .ingest_transcribe import segments_from_whisper as segments_from_whisper
from .proc import run
from .workspace import Workspace
from .workspace_lock import stable_workspace_lock


class _AsrBackendTrace(threading.local):
    """직전 _transcribe가 실제로 쓴 백엔드 — "mlx" | "faster-whisper" |
    "faster-whisper(mlx-fallback)". 반환 계약(3-튜플)에 실을 수 없는 이유:
    테스트·외부 호출자 다수가 `ingest._transcribe`를 3-튜플 fake로 패치한다.
    오케스트레이션이 호출 직후 읽어 최종 채택 전사에 결박하는 out-of-band
    슬롯이며, thread-local이라 같은 프로세스의 병렬 ingest()가 서로의
    라벨을 덮지 않는다. fake 경로는 슬롯을 안 채우므로 None."""

    label: str | None = None


_ASR_TRACE = _AsrBackendTrace()


def _transcribe(
    wav: Path,
    model: str,
    lang: str | None,
    hotwords: str | None = None,
    condition_on_previous_text: bool = True,
    backend: str = "auto",
) -> tuple[list[Segment], list[dict], str | None]:
    """백엔드 선택 + MLX 실패 폴백 — 파사드 소유가 호환 계약의 일부다.

    폴백은 이 모듈 전역의 `_transcribe_faster_whisper`를 통한다: 분해 전부터
    `ingest._transcribe_faster_whisper`를 setattr로 패치하고 `_transcribe`를
    부르는 호출자가 있고, 이 함수가 `ingest_transcribe`로 내려가면 그 패치가
    조용히 무시된다(리뷰 2026-07-26).
    """
    from .runtime_config import (
        ASRBackend,
        Feature,
        feature_enabled,
        load_runtime_config,
        resolve_asr_backend,
    )

    config = load_runtime_config()
    if not feature_enabled(Feature.ASR, config):
        raise RuntimeError(
            "asr 기능이 꺼져 있습니다 — `va runtime set feature.asr on`으로 "
            "켜십시오"
        )
    requested = ASRBackend(backend)
    if requested is not ASRBackend.AUTO:
        config = replace(config, asr_backend=requested)
    selected = resolve_asr_backend(config)
    fallback_label = "faster-whisper"
    if selected is ASRBackend.MLX:
        from .asr_mlx import transcribe_mlx

        try:
            result = transcribe_mlx(
                wav,
                model,
                lang,
                hotwords,
                condition_on_previous_text=condition_on_previous_text,
            )
            _ASR_TRACE.label = "mlx"
            return result
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            print(
                "MLX Whisper 품질/실행 가드 → faster-whisper 폴백 "
                f"({exc})",
                file=sys.stderr,
            )
            fallback_label = "faster-whisper(mlx-fallback)"
    result = _transcribe_faster_whisper(
        wav,
        model,
        lang,
        hotwords,
        condition_on_previous_text=condition_on_previous_text,
    )
    _ASR_TRACE.label = fallback_label
    return result


def _transcribe_selected(
    wav: Path,
    model: str,
    lang: str | None,
    hotwords: str | None,
    *,
    backend: str,
    condition_on_previous_text: bool = True,
) -> tuple[list[Segment], list[dict], str | None]:
    """Preserve the old monkeypatch/call contract for the default path."""
    if backend == "auto":
        if condition_on_previous_text:
            return _transcribe(wav, model, lang, hotwords)
        return _transcribe(
            wav,
            model,
            lang,
            hotwords,
            condition_on_previous_text=False,
        )
    return _transcribe(
        wav,
        model,
        lang,
        hotwords,
        condition_on_previous_text=condition_on_previous_text,
        backend=backend,
    )


def _retranscribe_tail(
    wav: Path,
    stall: float,
    model: str,
    lang: str | None,
    hotwords: str | None,
    *,
    backend: str,
) -> tuple[list[Segment], list[dict], str | None]:
    """붕괴 지점 이후 구간만 다시 받아써 워크스페이스 시간축으로 되돌린다.

    꼬리 wav를 임시로 잘라 전사하고 타임스탬프를 stall만큼 되민다.
    추출 실패는 빈 결과 — 호출자의 "꼬리 무발화 = 원본 유지"와 동치로
    합류시켜 복구 실패가 전사를 잃게 하지 않는다.
    """
    with tempfile.TemporaryDirectory(prefix="va-tail-") as scratch:
        tail_wav = Path(scratch) / "tail.wav"
        extracted = run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{stall:.3f}",
             "-i", str(wav), "-c:a", "pcm_s16le", str(tail_wav)],
            capture_output=True, text=True,
        )
        if extracted.returncode != 0 or not tail_wav.is_file():
            print(
                f"꼬리 추출 실패 — 복구를 건너뜁니다: "
                f"{extracted.stderr.strip()}",
                file=sys.stderr,
            )
            return [], [], None
        tail_segments, tail_words, tail_language = _transcribe_selected(
            tail_wav,
            model,
            lang,
            hotwords,
            backend=backend,
            condition_on_previous_text=False,
        )
    for segment in tail_segments:
        segment.start = round(segment.start + stall, 3)
        segment.end = round(segment.end + stall, 3)
    shifted_words = [
        {**word, "start": round(word["start"] + stall, 3),
         "end": round(word["end"] + stall, 3)}
        for word in tail_words
    ]
    return tail_segments, shifted_words, tail_language


def _ingest_workspace_root(video: Path, out: Path | None) -> Path:
    """Resolve the workspace path before any manifest makes it GC-visible."""
    source = str(video)
    if out is not None:
        return Path(out)
    if source.startswith(("http://", "https://")):
        return (
            Path.cwd()
            / "va-out"
            / ("url-" + hashlib.md5(source.encode()).hexdigest()[:8])
        )
    return Path.cwd() / "va-out" / Path(video).resolve().stem


@contextmanager
def ingest_session(
    video: Path,
    out: Path | None = None,
    model: str = "small",
    lang: str | None = None,
    force_whisper: bool = False,
    max_height: int = 1080,
    hotwords: str | None = None,
    cookies_from_browser: str | None = None,
    signals: bool = False,
    asr_backend: str = "auto",
) -> Iterator[Workspace]:
    """Yield a built workspace while excluding whole-workspace GC.

    CLI callers keep the context open through post-ingest rendering so a
    concurrent GC cannot remove the just-published workspace between ingest
    completion and manifest/brief output.
    """
    source = str(video)
    frozen_video = (
        video
        if source.startswith(("http://", "https://"))
        else Path(video).resolve()
    )
    root = _ingest_workspace_root(frozen_video, out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    pending = Workspace(root)
    if not pending.manifest_path.is_file():
        pending.workspace_lock_path.touch(mode=0o600, exist_ok=True)
    with stable_workspace_lock(
        pending.root,
        pending.workspace_lock_path,
        exclusive=True,
    ):
        from .revision import (
            _write_building_manifest,
            assert_ingest_target_unused,
        )

        assert_ingest_target_unused(root, frozen_video)
        _write_building_manifest(root, frozen_video)
        yield _ingest_locked(
            frozen_video,
            out=root,
            model=model,
            asr_backend=asr_backend,
            lang=lang,
            force_whisper=force_whisper,
            max_height=max_height,
            hotwords=hotwords,
            cookies_from_browser=cookies_from_browser,
            signals=signals,
        )


def ingest(
    video: Path,
    out: Path | None = None,
    model: str = "small",
    lang: str | None = None,
    force_whisper: bool = False,
    max_height: int = 1080,
    hotwords: str | None = None,
    cookies_from_browser: str | None = None,
    signals: bool = False,
    asr_backend: str = "auto",
) -> Workspace:
    """Build one workspace while excluding concurrent whole-workspace GC."""
    with ingest_session(
        video,
        out=out,
        model=model,
        asr_backend=asr_backend,
        lang=lang,
        force_whisper=force_whisper,
        max_height=max_height,
        hotwords=hotwords,
        cookies_from_browser=cookies_from_browser,
        signals=signals,
    ) as workspace:
        return workspace


def _ingest_locked(
    video: Path,
    out: Path | None = None,
    model: str = "small",
    lang: str | None = None,
    force_whisper: bool = False,
    max_height: int = 1080,
    hotwords: str | None = None,
    cookies_from_browser: str | None = None,
    signals: bool = False,
    asr_backend: str = "auto",
) -> Workspace:
    from .subtitles import find_subtitle_cues

    source_url = None
    src = str(video)
    if src.startswith(("http://", "https://")):
        source_url = src
        dest_dir = Path(out) if out else (
            Path.cwd() / "va-out"
            / ("url-" + hashlib.md5(src.encode()).hexdigest()[:8])
        )
        video = _download_url(src, dest_dir, max_height,
                              cookies_from_browser=cookies_from_browser)
        _promote_subtitle(Path(video))
        # 사람이 만든 자막이 없을 때만 업로더의 자동자막으로 물러선다 —
        # 그마저 없을 때가 whisper 차례다(가장 비싼 경로가 마지막).
        if not force_whisper and find_subtitle_cues(Path(video)) is None:
            if _download_auto_subs(src, dest_dir, cookies_from_browser):
                _promote_subtitle(Path(video))
                print("업로더 자동자막을 씁니다 — 오디오 재전사 생략",
                      file=sys.stderr)
        out = dest_dir

    ws = Workspace.create(video, out=out)
    from .revision import seal_ingest_source

    seal_ingest_source(ws)
    # 사전지식은 전사보다 먼저 확정한다 — 그래야 hotwords 프라이어로 쓸 수 있다.
    from .priors import local_priors, read_source_priors

    manifest = ws.manifest
    if source_url:
        manifest["source_url"] = source_url
        manifest.update(read_source_priors(ws.root))
    else:
        manifest.update(local_priors(Path(ws.video)))
    ws.save_manifest(manifest)
    source = "whisper"
    words: list[dict] = []
    applied_hotwords: str | None = None
    rejected_hotwords: str | None = None
    speech_seconds: float | None = None
    repair: str | None = None
    # 최종 채택 전사를 만든 백엔드 — 자막·무오디오 경로와 fake 패치 경로는
    # None으로 남는다(whisper가 실행되지 않았다는 뜻).
    asr_backend_used: str | None = None
    cues = None if force_whisper else find_subtitle_cues(ws.video)
    if cues:
        from .subtitles import dedup_rolling_cues

        deduped = dedup_rolling_cues(cues)
        if len(deduped) < len(cues):
            print(f"자동자막 롤링 정규화: {len(cues)} → {len(deduped)} 세그",
                  file=sys.stderr)
        cues = deduped
        segments = [
            Segment(id=i, start=round(s, 3), end=round(e, 3), text=text)
            for i, (s, e, text) in enumerate(cues)
        ]
        language = None
        source = "subtitles"
    elif not ws.manifest["has_audio"]:
        segments = []
        language = None
    else:
        if hotwords is None:
            from .glossary import load_hotwords
            from .priors import prior_hotwords

            # 이 영상의 메타데이터에서 온 용어가 코퍼스 글로서리보다 우선한다 —
            # 전자는 이 오디오와 관련됨이 정의상 보장되고, 후자는 추정이다.
            # whisper는 잘릴 때 프롬프트 끝을 남기므로 프라이어를 뒤에 둔다.
            prior = prior_hotwords(ws.manifest)
            shared = load_hotwords() or ""
            hotwords = " ".join(x for x in (shared, prior) if x) or None
            if prior:
                print(f"source priors hotwords: {len(prior.split())}개 "
                      f"(제목·업로더·설명 유래)", file=sys.stderr)
            if shared:
                print(f"glossary hotwords 적용: {len(shared.split())}개 용어",
                      file=sys.stderr)
        # 추출은 워크스페이스 캐시로 — 같은 wav를 이후 diarize/
        # audioevents가 재사용한다(명령마다 재추출하던 것을 봉합).
        wav = cached_audio_wav(ws)
        # 같은 스레드의 이전 실행이 남긴 라벨이 fake 패치 경로에 새지
        # 않도록 호출 전 슬롯을 비운다.
        _ASR_TRACE.label = None
        segments, words, language = _transcribe_selected(
            wav,
            model,
            lang,
            hotwords,
            backend=asr_backend,
        )
        asr_backend_used = _ASR_TRACE.label
        applied_hotwords = hotwords
        if hotwords and _hotword_contaminated(segments, hotwords):
            # 무발화·BGM 영상에서 hotwords가 반복 환각을 만든다(2회 실측:
            # 2026-07-13·07-22, 17/17 동일문장 conf 0.9까지). conf로는 못
            # 잡고 반복성으로 잡는다 — hotwords 없이 재전사해 채택.
            print("hotwords 오염 의심(글로서리 용어 반복 환각) — "
                  "hotwords 없이 재전사", file=sys.stderr)
            _ASR_TRACE.label = None
            segments, words, language = _transcribe_selected(
                wav,
                model,
                lang,
                None,
                backend=asr_backend,
            )
            asr_backend_used = _ASR_TRACE.label
            applied_hotwords, rejected_hotwords = None, hotwords
            print(f"재전사 채택: {len(segments)} segments (hotwords 제외)",
                  file=sys.stderr)
        segments = resegment_by_words(segments, words)
        # 조용한 절단 방어 — 붕괴는 성공으로 보고되고, 그 잘린 전사가
        # 아래 brief에서 "발화 19%·시각 주도"라는 결론으로 승격된다.
        speech_seconds = _vad_speech_seconds(wav) if segments else None
        if _transcript_collapsed(segments, speech_seconds):
            # 붕괴의 대표 형상은 "도중에 죽음"(실측 2026-07-25: 1092초
            # 정지, 앞부분 정상)이지만, 판정식은 총량만 보므로 "중간에
            # 구멍이 뚫리고 마지막 큐만 살아남은" 형상도 통과한다. 두
            # 형상은 복구 전략이 다르다: 프리픽스가 그 자체로 건강할 때만
            # 멈춘 지점 이후를 다시 받아쓰고(전체 재디코딩의 절반 이하
            # 비용·프리픽스 보존이라 손실 불가), 아니면 종전대로 전체를
            # 문맥 이어받기 없이 다시 받아써 비교 채택한다.
            stall = segments[-1].end
            speech_chunks = _vad_speech_chunks(wav)
            prefix_speech = (
                sum(
                    max(0.0, min(end, stall) - start)
                    for start, end in speech_chunks
                    if start < stall
                )
                if speech_chunks is not None
                else None
            )
            prefix_intact = prefix_speech is not None and (
                prefix_speech <= 1e-9
                or _spoken_seconds(segments) / prefix_speech
                >= COLLAPSE_COVERAGE
            )
            print(
                f"전사 붕괴 의심: 말소리 {speech_seconds:.0f}초 중 "
                f"{_spoken_seconds(segments):.0f}초만 옮겨 적혔습니다 "
                f"(마지막 {stall:.0f}초에서 멈춤) — "
                + ("멈춘 지점부터 다시 받아씁니다"
                   if prefix_intact
                   else "빠진 발화가 그 앞에도 있어 전체를 문맥 이어받기 "
                        "없이 다시 받아씁니다"),
                file=sys.stderr,
            )
            if prefix_intact:
                _ASR_TRACE.label = None
                tail_segments, tail_words, tail_language = _retranscribe_tail(
                    wav,
                    stall,
                    model,
                    lang,
                    applied_hotwords,
                    backend=asr_backend,
                )
                # 꼬리 재시도가 다른 백엔드로 흘렀을 수 있다(예: MLX 폴백).
                tail_backend_used = _ASR_TRACE.label
                tail_segments = resegment_by_words(tail_segments, tail_words)
                if _spoken_seconds(tail_segments) > 0:
                    segments = segments + tail_segments
                    for index, segment in enumerate(segments):
                        segment.id = index
                    words = words + tail_words
                    language = language or tail_language
                    if (tail_backend_used
                            and tail_backend_used != asr_backend_used):
                        asr_backend_used = (
                            f"{asr_backend_used}+{tail_backend_used}(tail)"
                        )
                    repair = f"tail-retranscribe(from={stall:.1f}s)"
                    print(
                        f"꼬리 복구 채택: +{len(tail_segments)}개 문장 · "
                        f"총 {_spoken_seconds(segments):.0f}초",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "멈춘 지점 이후에서 말소리를 얻지 못했습니다 — 처음 "
                        "것을 그대로 씁니다(말소리가 적은 영상일 수 있음)",
                        file=sys.stderr,
                    )
            else:
                # 중간 구멍 형상(또는 VAD 불가로 형상 불명) — 꼬리 복구는
                # 구멍을 못 메우므로 종전 전체 재시도를 폴백으로 보존한다.
                _ASR_TRACE.label = None
                alt, alt_words, alt_language = _transcribe_selected(
                    wav,
                    model,
                    lang,
                    applied_hotwords,
                    backend=asr_backend,
                    condition_on_previous_text=False,
                )
                alt_backend_used = _ASR_TRACE.label
                alt = resegment_by_words(alt, alt_words)
                if _spoken_seconds(alt) > _spoken_seconds(segments):
                    segments, words, language = alt, alt_words, alt_language
                    asr_backend_used = alt_backend_used
                    repair = "condition_on_previous_text=False"
                    print(
                        f"다시 받아쓴 쪽을 씁니다: {len(segments)}개 문장 · "
                        f"{_spoken_seconds(segments):.0f}초",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "다시 받아써도 늘지 않았습니다 — 처음 것을 그대로 "
                        "씁니다(말소리가 적은 영상일 수 있음)",
                        file=sys.stderr,
                    )
    if not segments:
        # cause-specific diagnosis: say WHY the transcript is empty instead
        # of a generic warning
        if not ws.manifest.get("has_audio"):
            print("무발화 진단: 오디오 트랙 자체가 없음 — 시각 전용 루프로 진행",
                  file=sys.stderr)
        else:
            print(
                "무발화 진단: 오디오는 있으나 음성 미검출(BGM/효과음/무대음 추정) "
                "— scenes→필름스트립 폴백, 자막 보이면 ocr --every 스캔",
                file=sys.stderr,
            )
    write_text_atomic(
        ws.transcript_path,
        json.dumps(
            [{k: v for k, v in asdict(s).items() if v is not None}
             for s in segments],
            ensure_ascii=False, indent=1,
        ),
    )
    _write_srt(segments, ws.srt_path)
    if words:
        write_text_atomic(ws.root / "words.json",
                          json.dumps(words, ensure_ascii=False))
    else:
        # A failed building attempt may have written words before publication.
        # A same-source retry with subtitles/no speech must not publish them.
        (ws.root / "words.json").unlink(missing_ok=True)
    manifest = ws.manifest
    # 전사 설정 계보 스탬핑 — 어떤 hotwords 세대의 산물인지(오염으로 제외된
    # 경우 그 사실까지)가 남아야 corrections 코퍼스와 재전사 판단이 선다.
    manifest.update({"whisper_model": model, "language": language,
                     "segments": len(segments), "words": len(words),
                     "transcript_source": source,
                     # 실제 사용된 ASR 백엔드(MLX 폴백 여부 포함) — 지각
                     # 산출물 감사의 마지막 공백. whisper 미실행이면 None.
                     "asr_backend": asr_backend_used,
                     "hotwords": applied_hotwords,
                     "hotwords_rejected": rejected_hotwords,
                     # 붕괴는 조용하다 — 정상 실행도 커버리지를 남겨야
                     # 사후에 "이 전사를 믿어도 되는가"를 감사할 수 있다.
                     "transcript_coverage": (
                         round(_spoken_seconds(segments) / speech_seconds, 3)
                         if speech_seconds else None),
                     "transcript_repair": repair})
    ws.save_manifest(manifest)
    from .revision import publish_workspace_revisions

    publish_workspace_revisions(ws)
    if signals:
        from .highlights import compute_highlights
        from .scenes import compute_scenes

        compute_highlights(ws)
        compute_scenes(ws, adaptive=True)
    return ws
