"""워크스페이스 지문 기반 투영 캐시 — 변경 없는 재투영을 건너뛴다.

`va view`/`va index`/`va wiki`는 코퍼스가 커져도 항상 전 워크스페이스를
다시 렌더했다(전수점검 2026-07-26 실측 확정 항목). 원장·산출물 입력
파일들의 (크기, mtime_ns) 지문이 그대로면 같은 입력이고, 모든 투영은
결정적이므로 같은 출력이다 — 다시 쓰지 않는다.

- 내용 해시가 아니라 stat 지문인 이유: 이 리포의 쓰기는 전부 원자 교체
  (write_text_atomic)라 mtime이 항상 갱신된다. 파일을 읽지 않고 판별한다.
- 각 투영은 포맷 솔트를 지문에 섞는다. 솔트는 `renderer_salt()`가 렌더러
  소스 digest에서 자동 유도한다 — 수동 버전 문자열은 렌더러를 고치고
  범프를 잊는 순간 기존 코퍼스가 낡은 렌더를 영구 스킵한다(2026-07-26
  실측 사고, view v1→v2). 소스 바이트가 곧 포맷 버전이면 그 계약이
  기계로 닫힌다.
- 캐시 파일이 없거나 깨져 있으면 전체 재투영으로 조용히 폴백한다.
  강제 재투영이 필요하면 `<corpus>/.tca-projection-cache.json`을 지운다.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from .fsio import write_text_atomic

CACHE_FILENAME = ".tca-projection-cache.json"


def _tree_source_digest(root: Path) -> str:
    """디렉터리 아래 전체 .py 소스의 결정적 digest (이름순)."""
    digest = hashlib.sha256()
    for source in sorted(root.rglob("*.py")):
        digest.update(source.relative_to(root).as_posix().encode())
        try:
            digest.update(source.read_bytes())
        except OSError:
            # 소스가 없는 배포(pyc 전용 등)는 경로명으로 강등 — 렌더 변경
            # 추적은 잃지만 빌드가 죽는 것보다 낫다(구 수동 솔트와 동급).
            digest.update(b"?")
    return digest.hexdigest()[:12]


@lru_cache(maxsize=None)
def renderer_salt(prefix: str) -> str:
    """패키지 전체 소스 digest에서 유도한 포맷 솔트 — 코드 버전=포맷 버전.

    렌더러 모듈을 사람이 나열하는 방식은 재수출·간접 의존을 놓친다
    (리뷰 2026-07-26 P1: sequence.py가 sequence_projection의 재수출이라
    구현 변경이 지문에 안 잡힘) — 수동 솔트 범프를 잊던 사고와 같은
    계열의 "잊음"이 모듈 단위로 재발한다. 패키지 전체를 지문화하면
    누락이 불가능하다. 대가는 코드 변경 시 코퍼스당 1회 재렌더(수 초),
    반대급부는 낡은 렌더 영구화 불가. CLI는 명령마다 새 프로세스라
    lru_cache 수명은 한 실행이고 비용은 수 ms다.
    """
    return f"{prefix}-{_tree_source_digest(Path(__file__).resolve().parent)}"

# 투영이 읽는 워크스페이스 입력들 — 여기 없는 파일은 투영 결과에 영향을
# 주지 않아야 한다. frames/ 디렉터리는 파일 목록 변화(gc --purge 포함)를
# 디렉터리 mtime으로 잡는다.
_INPUT_FILES = (
    "manifest.json",
    "transcript.json",
    "checkpoints.jsonl",
    "corrections.jsonl",
    "sequences.jsonl",
    "image-provenance.jsonl",
    "ocr_transcript.json",
    "highlights.json",
    "scenes.json",
    "audio_events.json",
    "diarization.json",
    "title.txt",
    "uploader.txt",
    "description.txt",
)


def workspace_fingerprint(
    ws_dir: Path, *, salt: str, extra_paths: list[Path] | None = None
) -> str:
    parts = [salt]
    for extra in extra_paths or []:
        try:
            extra_stat = extra.stat()
            parts.append(
                f"extra:{extra.name}:{extra_stat.st_size}:"
                f"{extra_stat.st_mtime_ns}"
            )
        except OSError:
            parts.append(f"extra:{extra.name}:absent")
    for name in _INPUT_FILES:
        try:
            stat = (ws_dir / name).stat()
            parts.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
        except OSError:
            parts.append(f"{name}:absent")
    try:
        frames_stat = (ws_dir / "frames").stat()
        parts.append(f"frames:{frames_stat.st_mtime_ns}")
    except OSError:
        parts.append("frames:absent")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def files_fingerprint(paths: list[Path], *, salt: str) -> str:
    """개별 산출물 지문 — 산출물이 입력을 겸할 때(수기 서사가 든 scene-log)
    바깥 편집을 스킵이 놓치지 않게 한다."""
    parts = [salt]
    for path in paths:
        try:
            stat = path.lstat()
            parts.append(
                f"{path.name}:{stat.st_mode}:{stat.st_size}:{stat.st_mtime_ns}"
            )
        except OSError:
            parts.append(f"{path.name}:absent")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def directory_fingerprint(directory: Path, *, salt: str) -> str:
    """출력 트리 지문 — 재생성 스킵이 훼손(심링크·중복·수기 편집)을 놓치지
    않게 한다. lstat 기반(mode 포함)이라 심링크 식별·권한 변경도 잡힌다."""
    parts = [salt]
    if directory.is_dir():
        for path in sorted(directory.rglob("*")):
            try:
                stat = path.lstat()
            except OSError:
                parts.append(f"{path.name}:unstattable")
                continue
            parts.append(
                f"{path.relative_to(directory).as_posix()}:"
                f"{stat.st_mode}:{stat.st_size}:{stat.st_mtime_ns}"
            )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def load_projection_cache(root: Path) -> dict:
    try:
        cache = json.loads(
            (root / CACHE_FILENAME).read_text(encoding="utf-8")
        )
        return cache if isinstance(cache, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_projection_cache(
    root: Path, cache: dict, *, live_keys: set[str]
) -> None:
    """섹션별 ws 키를 현재 코퍼스로 가지치기해 저장 — 삭제된 ws가 안 남게."""
    pruned = {}
    for section, value in cache.items():
        if isinstance(value, dict) and section in ("view", "index"):
            pruned[section] = {
                key: fp for key, fp in value.items() if key in live_keys
            }
        else:
            pruned[section] = value
    try:
        write_text_atomic(
            root / CACHE_FILENAME,
            json.dumps(pruned, ensure_ascii=False, indent=1),
        )
    except OSError:
        pass  # 캐시는 최적화일 뿐 — 저장 실패가 투영을 깨면 안 된다
