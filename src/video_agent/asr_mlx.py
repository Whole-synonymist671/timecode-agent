"""Apple-Silicon MLX Whisper adapter with VAD and output quality gates."""

from __future__ import annotations

import platform
import sys
from collections import Counter
from pathlib import Path
from typing import Mapping, TypeAlias

from .ingest import Segment

JsonValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)

_MODEL_REPOS = {
    "tiny": "mlx-community/whisper-tiny",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}
_MAX_COMPRESSION_RATIO = 2.4


def mlx_model_repo(model: str) -> str:
    return _MODEL_REPOS.get(model, model)


def _number(value: JsonValue) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _mapping(value: JsonValue) -> Mapping[str, JsonValue] | None:
    if not isinstance(value, dict):
        return None
    return value


def _repetition_ratio(text: str) -> float:
    tokens = text.split()
    if len(tokens) < 12:
        return 0.0
    return Counter(tokens).most_common(1)[0][1] / len(tokens)


def segments_from_mlx_result(
    result: Mapping[str, JsonValue],
) -> tuple[list[Segment], list[dict], str | None]:
    """Validate MLX output before it can replace the stable ASR path."""
    raw_segments = result.get("segments")
    if not isinstance(raw_segments, list):
        raise RuntimeError("MLX Whisper 결과에 segments가 없습니다")

    segments: list[Segment] = []
    words_all: list[dict] = []
    for raw_segment in raw_segments:
        segment = _mapping(raw_segment)
        if segment is None:
            continue
        text_value = segment.get("text")
        text = text_value.strip() if isinstance(text_value, str) else ""
        if not text:
            continue
        compression = _number(segment.get("compression_ratio"))
        if (
            compression is not None
            and compression > _MAX_COMPRESSION_RATIO
        ) or _repetition_ratio(text) >= 0.6:
            raise RuntimeError(
                "MLX Whisper 품질 가드: 반복 환각 또는 과도한 압축률"
            )

        start = _number(segment.get("start"))
        end = _number(segment.get("end"))
        if start is None or end is None:
            continue
        probabilities: list[float] = []
        raw_words = segment.get("words")
        if isinstance(raw_words, list):
            for raw_word in raw_words:
                word = _mapping(raw_word)
                if word is None:
                    continue
                word_start = _number(word.get("start"))
                word_end = _number(word.get("end"))
                word_text = word.get("word")
                probability = _number(word.get("probability"))
                if (
                    word_start is None
                    or word_end is None
                    or not isinstance(word_text, str)
                ):
                    continue
                if probability is not None:
                    probabilities.append(probability)
                words_all.append(
                    {
                        "start": round(word_start, 3),
                        "end": round(word_end, 3),
                        "word": word_text,
                        "p": round(probability, 3)
                        if probability is not None
                        else 0.0,
                    }
                )
                end = word_end
        avg_logprob = _number(segment.get("avg_logprob"))
        no_speech = _number(segment.get("no_speech_prob"))
        if avg_logprob is not None and avg_logprob < -1.0:
            raise RuntimeError("MLX Whisper 품질 가드: 평균 로그확률이 너무 낮음")
        if probabilities and sum(probabilities) / len(probabilities) < 0.1:
            raise RuntimeError("MLX Whisper 품질 가드: 단어 신뢰도가 너무 낮음")
        segments.append(
            Segment(
                id=len(segments),
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                logprob=round(avg_logprob, 3)
                if avg_logprob is not None
                else None,
                no_speech_prob=round(no_speech, 3)
                if no_speech is not None
                else None,
                conf=round(sum(probabilities) / len(probabilities), 3)
                if probabilities
                else None,
            )
        )
    language_value = result.get("language")
    language = language_value if isinstance(language_value, str) else None
    return segments, words_all, language


def _speech_clip_timestamps(wav: Path) -> list[float]:
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(str(wav), sampling_rate=16000)
    if isinstance(audio, tuple):
        raise RuntimeError("MLX VAD가 지원하지 않는 다중 채널 오디오입니다")
    chunks = get_speech_timestamps(audio, VadOptions())
    timestamps: list[float] = []
    for chunk in chunks:
        timestamps.extend((chunk["start"] / 16000, chunk["end"] / 16000))
    return timestamps


def transcribe_mlx(
    wav: Path,
    model: str,
    lang: str | None,
    hotwords: str | None = None,
    *,
    condition_on_previous_text: bool = True,
) -> tuple[list[Segment], list[dict], str | None]:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise RuntimeError("MLX Whisper는 Apple Silicon macOS에서만 지원됩니다")
    clips = _speech_clip_timestamps(wav)
    if not clips:
        return [], [], lang

    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(wav),
        path_or_hf_repo=mlx_model_repo(model),
        language=lang,
        initial_prompt=hotwords,
        word_timestamps=True,
        clip_timestamps=clips,
        condition_on_previous_text=condition_on_previous_text,
        hallucination_silence_threshold=1.0,
        # mlx_whisper 규약: None=침묵, False=tqdm 진행바, True=텍스트 출력.
        # 사람 터미널에서만 진행바를 켠다(비-tty는 기존과 동일하게 침묵).
        verbose=False if sys.stderr.isatty() else None,
    )
    parsed = segments_from_mlx_result(result)
    if not parsed[0]:
        raise RuntimeError(
            "MLX Whisper 품질 가드: VAD 발화 구간에서 전사 결과가 비어 있음"
        )
    return parsed
