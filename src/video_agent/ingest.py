"""Ingest: probe + audio extraction + whisper transcription -> transcript.json/srt."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterator

from .fsio import write_text_atomic
from .proc import run
from .workspace import Workspace
from .workspace_lock import stable_workspace_lock


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    # ASR-level uncertainty (VideoAgent2 "tool confidence"): low logprob or
    # high no_speech_prob marks a segment whose *transcription itself* is
    # suspect — an independent frame-verification trigger.
    logprob: float | None = None
    no_speech_prob: float | None = None
    # Mean word-level probability — discriminative per segment, unlike
    # avg_logprob which faster-whisper shares across a decode window.
    conf: float | None = None


def segments_from_whisper(raw_segments) -> tuple[list[Segment], list[dict]]:
    """Whisper segments -> (Segments, word list).

    Word timestamps fix the VAD silent-tail problem: a cue like "두 개 더"
    stretched to 42s ends at its last spoken word instead.
    """
    segments: list[Segment] = []
    words_all: list[dict] = []
    for s in raw_segments:
        text = s.text.strip()
        if not text:
            continue
        end = s.end
        conf = None
        words = list(s.words) if getattr(s, "words", None) else []
        if words:
            end = words[-1].end
            conf = round(sum(w.probability for w in words) / len(words), 3)
            words_all.extend(
                {"start": round(w.start, 3), "end": round(w.end, 3),
                 "word": w.word, "p": round(w.probability, 3)}
                for w in words
            )
        segments.append(Segment(
            id=len(segments), start=round(s.start, 3), end=round(end, 3),
            text=text,
            logprob=round(s.avg_logprob, 3)
            if s.avg_logprob is not None else None,
            no_speech_prob=round(s.no_speech_prob, 3)
            if s.no_speech_prob is not None else None,
            conf=conf,
        ))
    return segments, words_all


def _extract_audio(video: Path, wav: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(wav),
    ]
    res = run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"ffmpeg audio extract failed ({res.returncode}): {res.stderr.strip()}"
        )


def _transcribe_faster_whisper(
    wav: Path, model: str, lang: str | None, hotwords: str | None = None,
    condition_on_previous_text: bool = True,
) -> tuple[list[Segment], list[dict], str | None]:
    from faster_whisper import WhisperModel  # lazy: heavy import + model download

    wm = WhisperModel(model, device="cpu", compute_type="int8")
    raw_segments, info = wm.transcribe(
        str(wav), language=lang, vad_filter=True, word_timestamps=True,
        hotwords=hotwords,
        condition_on_previous_text=condition_on_previous_text,
    )
    segments, words = segments_from_whisper(raw_segments)
    return segments, words, getattr(info, "language", None)


def _transcribe(
    wav: Path,
    model: str,
    lang: str | None,
    hotwords: str | None = None,
    condition_on_previous_text: bool = True,
    backend: str = "auto",
) -> tuple[list[Segment], list[dict], str | None]:
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
    if selected is ASRBackend.MLX:
        from .asr_mlx import transcribe_mlx

        try:
            return transcribe_mlx(
                wav,
                model,
                lang,
                hotwords,
                condition_on_previous_text=condition_on_previous_text,
            )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            print(
                "MLX Whisper 품질/실행 가드 → faster-whisper 폴백 "
                f"({exc})",
                file=sys.stderr,
            )
    return _transcribe_faster_whisper(
        wav,
        model,
        lang,
        hotwords,
        condition_on_previous_text=condition_on_previous_text,
    )


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


# 붕괴 판정 임계 — 실측(2026-07-25, 43분 영상): 붕괴 0.211 vs 정상 0.8x.
COLLAPSE_COVERAGE = 0.5
# 짧은 오디오는 이 비율이 요란하다(한 문장이 전체를 좌우) — 게이트 면제.
COLLAPSE_MIN_SPEECH = 60.0


def _vad_speech_seconds(wav: Path) -> float | None:
    """전사가 쓰는 것과 같은 VAD로 본 발화 총량 — 붕괴 판정의 접지선.

    전사량이 적은 게 '조용한 영상'인지 '죽은 디코딩'인지는 전사문만
    봐서는 구분되지 않는다. 판정에는 전사와 독립된 기준이 필요하다.
    """
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError:      # 불완전 설치여도 VAD 판정만 건너뛰고 전사는 계속
        return None
    try:
        audio = decode_audio(str(wav), sampling_rate=16000)
        if isinstance(audio, tuple):
            return None
        chunks = get_speech_timestamps(audio, VadOptions())
    except Exception:        # VAD 실패로 전사를 버릴 이유는 없다 (fail-open)
        return None
    return sum(c["end"] - c["start"] for c in chunks) / 16000


def _spoken_seconds(segments: list[Segment]) -> float:
    return sum(s.end - s.start for s in segments)


def _transcript_collapsed(
    segments: list[Segment], speech_seconds: float | None
) -> bool:
    """장문 디코딩이 도중에 죽었는지 — VAD 발화 대비 전사량으로 본다.

    faster-whisper 장문 루프는 오염된 이전 문맥이 프롬프트에 들어가면
    이후 윈도를 계속 no-speech로 흘려보내다 조용히 멈춘다. 예외도 0이 아닌
    exit code도 남지 않는다 — 실측(2026-07-25, 43분 영상): 동일 입력 3회 중
    1회가 1092초에서 정지(발화 2303.8초 중 486.2초 = 0.211), 나머지 2회는
    757·895 세그먼트로 끝까지 완주. 비결정적이라 재현 대기가 아니라
    산출물 검사로 잡아야 한다.
    """
    if not speech_seconds or speech_seconds < COLLAPSE_MIN_SPEECH:
        return False
    return _spoken_seconds(segments) / speech_seconds < COLLAPSE_COVERAGE


def _hotword_contaminated(segments: list[Segment], hotwords: str) -> bool:
    """Repetition-based hallucination check.

    Measured margins (2026-07-22): contaminated no-speech run = hotword-hit
    1.00 / unique-text 0.18; legitimate term-heavy speech ≤ 0.13 / ≥ 0.96.
    Confidence cannot separate them (hallucinated conf reached 0.92).
    """
    if len(segments) < 5:
        return False
    terms = hotwords.split()
    hit = sum(1 for s in segments if any(t in s.text for t in terms))
    uniq = len({s.text.strip() for s in segments})
    return hit / len(segments) >= 0.5 and uniq / len(segments) <= 0.3


def _write_srt(segments: list[Segment], path: Path) -> None:
    def stamp(t: float) -> str:
        ms = round(t * 1000)
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1_000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = [
        f"{seg.id + 1}\n{stamp(seg.start)} --> {stamp(seg.end)}\n{seg.text}\n"
        for seg in segments
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


def resegment_by_words(
    segments: list[Segment],
    words: list[dict],
    max_gap: float = 1.5,
) -> list[Segment]:
    """Split merged segments at word-gaps > max_gap (BGM-show fix).

    Whisper VAD groups distant utterances into one segment when music sits
    between them; word timestamps reveal the real gaps.
    """
    if not words:
        return segments
    out: list[Segment] = []
    for seg in segments:
        seg_words = [w for w in words
                     if seg.start - 0.01 <= w["start"] < seg.end + 0.01]
        if len(seg_words) < 2:
            out.append(seg)
            continue
        groups: list[list[dict]] = [[seg_words[0]]]
        for prev, cur in zip(seg_words, seg_words[1:]):
            if cur["start"] - prev["end"] > max_gap:
                groups.append([cur])
            else:
                groups[-1].append(cur)
        if len(groups) == 1:
            out.append(seg)
            continue
        for g in groups:
            out.append(Segment(
                id=0,
                start=round(g[0]["start"], 3),
                end=round(g[-1]["end"], 3),
                text="".join(w["word"] for w in g).strip(),
                logprob=seg.logprob, no_speech_prob=seg.no_speech_prob,
                conf=round(sum(w.get("p", 0) for w in g) / len(g), 3)
                if g and g[0].get("p") is not None else seg.conf,
            ))
    for i, seg in enumerate(out):
        seg.id = i
    return out


_SUB_PRIORITY = (".ko.", ".ko-orig.", ".en.")


def _pick_subtitle(paths: list[Path]) -> Path | None:
    """Manual Korean > auto Korean > English (claude-video's caption-first)."""
    for marker in _SUB_PRIORITY:
        for p in paths:
            if marker in p.name:
                return p
    return paths[0] if paths else None


def _download_url(
    url: str,
    dest_dir: Path,
    max_height: int,
    cookies_from_browser: str | None = None,
) -> Path:
    """yt-dlp: resolution-capped video + subtitles into dest_dir.

    cookies_from_browser is passed through ONLY when the user asked for it
    (login-walled sites like Instagram) — never injected automatically.
    """
    import shutil as _shutil

    if _shutil.which("yt-dlp") is None:
        raise RuntimeError(
            "yt-dlp is required for URL ingest but was not found on PATH; "
            "install it with your OS package manager or the official yt-dlp "
            "release, then retry"
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp", url,
        "-f", f"bv*[height<={max_height}]+ba/b[height<={max_height}]",
        "--merge-output-format", "mp4",
        # uploader chapters -> mp4 chapter atoms -> probe() pre-map (free
        # semantic checkpoint drafts; no-op when the video has none)
        "--embed-chapters",
        # manual subtitles only: yt-dlp saves auto-captions under the same
        # <lang>.srt names, and their rolling/misheard text is worse than
        # whisper+hotwords — auto tracks measurably beat manual EN picks.
        "--write-subs",
        "--sub-langs", "ko,en", "--convert-subs", "srt",
        # subtitles are a bonus: a caption 429/absence must not abort the
        # video download itself (whisper is the fallback)
        "--ignore-errors",
        "--no-playlist", "--no-warnings", "-q",
        # 사람 표시 계층: 원제목을 함께 저장 — INDEX·scene-log 표시명의 원료
        # (url-<md5>류 기계 디렉터리명은 사람이 못 읽는다)
        "--print-to-file", "%(title)s", str(dest_dir / "title.txt"),
        # 업로더가 이미 적어 둔 사전지식 — 팀명·인명·대회명이 여기 정자로
        # 있는데 ASR은 그걸 처음 듣는 소리로 받아쓴다. 공짜로 얻어 두고
        # hotwords 프라이어와 브리핑 표시에 쓴다.
        "--print-to-file", "%(uploader)s", str(dest_dir / "uploader.txt"),
        "--print-to-file", "%(description)s", str(dest_dir / "description.txt"),
        "-o", str(dest_dir / "source.%(ext)s"),
    ]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    from .proc import DOWNLOAD_TIMEOUT
    res = run(cmd, timeout=DOWNLOAD_TIMEOUT, capture_output=True, text=True)
    video = dest_dir / "source.mp4"
    if res.returncode != 0 or not video.is_file():
        found = sorted(dest_dir.glob("source.*"))
        vids = [p for p in found if p.suffix in (".mp4", ".mkv", ".webm")]
        if not vids:
            if "Unsupported URL" in res.stderr:
                raise RuntimeError(
                    "yt-dlp가 지원하지 않는 사이트입니다 (Threads 등 — "
                    "지원 목록: yt-dlp --list-extractors). 브라우저에서 "
                    "영상 파일을 저장한 뒤 로컬 경로로 `va ingest` 하세요."
                )
            raise RuntimeError(
                f"yt-dlp download failed ({res.returncode}): "
                f"{res.stderr.strip()[-300:]}"
            )
        video = vids[0]
    return video


def _subtitle_sidecars(video: Path) -> list[Path]:
    return sorted(p for p in video.parent.glob(f"{video.stem}.*")
                  if p.suffix in (".srt", ".vtt") and p != video)


def _download_auto_subs(
    url: str, dest_dir: Path, cookies_from_browser: str | None = None
) -> bool:
    """수동 자막이 없을 때만 — 업로더 채널의 자동생성(ASR) 자막을 받는다.

    업로더가 이미 만들어 둔 전사를 두고 오디오를 처음부터 다시 받아쓸
    이유가 없다(실측 2026-07-25, 43분 영상: 자막 2.7초 vs whisper-small
    12분, 본문도 자막이 앞섬 — "세계 정복"을 whisper는 "세계정보"로 받아씀).

    원어 트랙(`*-orig`)을 먼저 요청한다: 자동자막의 다른 언어 트랙은 원어를
    기계번역한 것이라 원본을 직접 받아쓰는 것보다 나쁘다. 롤링 중복은
    dedup_rolling_cues가 정규화한다(자동 트랙 전용 장치).

    영상 다운로드와 호출을 분리하는 이유: yt-dlp가 자동자막도 <lang>.srt로
    저장해, 한 호출에 섞으면 사람이 만든 자막과 기계가 받아쓴 자막의
    구분이 소실된다.
    """
    from .proc import DOWNLOAD_TIMEOUT

    base = ["yt-dlp", url, "--skip-download", "--write-auto-subs",
            "--convert-subs", "srt", "--ignore-errors", "--no-playlist",
            "--no-warnings", "-q",
            "-o", str(dest_dir / "source.%(ext)s")]
    if cookies_from_browser:
        base += ["--cookies-from-browser", cookies_from_browser]
    probe = dest_dir / "source.mp4"
    for langs in (".*-orig", "ko,en"):
        run(base + ["--sub-langs", langs], timeout=DOWNLOAD_TIMEOUT,
            capture_output=True, text=True)
        if _subtitle_sidecars(probe):
            return True
    return False


def _promote_subtitle(video: Path) -> None:
    """source.ko.srt -> source.srt so find_subtitle_cues sees a sidecar."""
    best = _pick_subtitle(_subtitle_sidecars(video))
    if best:
        target = video.with_suffix(best.suffix)
        if not target.exists():
            best.rename(target)


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
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "audio.wav"
            _extract_audio(ws.video, wav)
            segments, words, language = _transcribe_selected(
                wav,
                model,
                lang,
                hotwords,
                backend=asr_backend,
            )
            applied_hotwords = hotwords
            if hotwords and _hotword_contaminated(segments, hotwords):
                # 무발화·BGM 영상에서 hotwords가 반복 환각을 만든다(2회 실측:
                # 2026-07-13·07-22, 17/17 동일문장 conf 0.9까지). conf로는 못
                # 잡고 반복성으로 잡는다 — hotwords 없이 재전사해 채택.
                print("hotwords 오염 의심(글로서리 용어 반복 환각) — "
                      "hotwords 없이 재전사", file=sys.stderr)
                segments, words, language = _transcribe_selected(
                    wav,
                    model,
                    lang,
                    None,
                    backend=asr_backend,
                )
                applied_hotwords, rejected_hotwords = None, hotwords
                print(f"재전사 채택: {len(segments)} segments (hotwords 제외)",
                      file=sys.stderr)
            segments = resegment_by_words(segments, words)
            # 조용한 절단 방어 — 붕괴는 성공으로 보고되고, 그 잘린 전사가
            # 아래 brief에서 "발화 19%·시각 주도"라는 결론으로 승격된다.
            speech_seconds = _vad_speech_seconds(wav) if segments else None
            if _transcript_collapsed(segments, speech_seconds):
                print(
                    f"전사 붕괴 의심: 말소리 {speech_seconds:.0f}초 중 "
                    f"{_spoken_seconds(segments):.0f}초만 옮겨 적혔습니다 "
                    f"(마지막 {segments[-1].end:.0f}초에서 멈춤) — 앞 문맥 "
                    f"이어받기를 끄고 다시 받아씁니다",
                    file=sys.stderr,
                )
                alt, alt_words, alt_language = _transcribe_selected(
                    wav,
                    model,
                    lang,
                    applied_hotwords,
                    backend=asr_backend,
                    condition_on_previous_text=False,
                )
                alt = resegment_by_words(alt, alt_words)
                if _spoken_seconds(alt) > _spoken_seconds(segments):
                    segments, words, language = alt, alt_words, alt_language
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
    manifest = ws.manifest
    # 전사 설정 계보 스탬핑 — 어떤 hotwords 세대의 산물인지(오염으로 제외된
    # 경우 그 사실까지)가 남아야 corrections 코퍼스와 재전사 판단이 선다.
    manifest.update({"whisper_model": model, "language": language,
                     "segments": len(segments), "words": len(words),
                     "transcript_source": source,
                     "hotwords": applied_hotwords,
                     "hotwords_rejected": rejected_hotwords,
                     # 붕괴는 조용하다 — 정상 실행도 커버리지를 남겨야
                     # 사후에 "이 전사를 믿어도 되는가"를 감사할 수 있다.
                     "transcript_coverage": (
                         round(_spoken_seconds(segments) / speech_seconds, 3)
                         if speech_seconds else None),
                     "transcript_repair": repair})
    ws.save_manifest(manifest)
    if signals:
        from .highlights import compute_highlights
        from .scenes import compute_scenes

        compute_highlights(ws)
        compute_scenes(ws, adaptive=True)
    return ws
