"""워크스페이스 오디오 캐시 — 같은 16kHz 모노 wav를 명령마다 재추출하지 않는다.

전사(ingest)·화자 분리(diarize)·의미 오디오 이벤트(audioevents)가 각자
임시 디렉터리에 동일한 변환을 반복했다(전수점검 2026-07-26 백로그).
워크스페이스 `cache/`에 한 번 추출해 두고 소스 영상 지문(크기·mtime)이
같으면 재사용한다.

- 디스크 비용은 대략 시간당 115MB — 재생성 가능한 파생물이므로
  `va gc --purge media`가 소스 영상과 같은 분류로 회수한다.
- 지문 메타는 wav 교체 *후*에 쓴다: 중간에 죽으면 지문 불일치로 남아
  다음 호출이 다시 추출한다(불완전 wav를 절대 신뢰하지 않는 순서).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .fsio import write_text_atomic
from .proc import run
from .workspace import Workspace

CACHE_DIRNAME = "cache"
_WAV_NAME = "audio-16k.wav"
_META_NAME = "audio-16k.json"


def _source_fingerprint(video: Path) -> dict:
    stat = video.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def existing_audio_wav(ws: Workspace) -> Path | None:
    """유효한 캐시가 이미 있으면 그 경로 — 없다고 추출하지는 않는다.

    highlights처럼 wav 없이도 한 번의 디코드로 끝나는 소비자용: 캐시가
    있으면 공짜로 빨라지고, 없다고 87MB짜리 파생물을 새로 만들지 않는다.
    """
    wav = ws.root / CACHE_DIRNAME / _WAV_NAME
    meta = ws.root / CACHE_DIRNAME / _META_NAME
    try:
        if (
            wav.is_file()
            and json.loads(meta.read_text(encoding="utf-8"))
            == _source_fingerprint(ws.video)
        ):
            return wav
    except (OSError, json.JSONDecodeError):
        pass
    return None


def cached_audio_wav(ws: Workspace) -> Path:
    """16kHz 모노 wav를 반환 — 유효 캐시 재사용, 없으면 추출 후 캐시."""
    cached = existing_audio_wav(ws)
    if cached is not None:
        return cached
    cache_dir = ws.root / CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    wav = cache_dir / _WAV_NAME
    with tempfile.NamedTemporaryFile(
        prefix=f".{_WAV_NAME}.", suffix=".tmp",
        dir=cache_dir, delete=False,
    ) as temporary_file:
        temporary = Path(temporary_file.name)
    try:
        result = run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(ws.video),
             "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(temporary)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg audio extract failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )
        temporary.replace(wav)
    finally:
        temporary.unlink(missing_ok=True)
    write_text_atomic(
        ws.root / CACHE_DIRNAME / _META_NAME,
        json.dumps(_source_fingerprint(ws.video)),
    )
    return wav
