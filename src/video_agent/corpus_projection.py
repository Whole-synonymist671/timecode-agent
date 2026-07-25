"""Shared corpus projections for Obsidian, wiki, index, and HTML views."""

from __future__ import annotations

import html
import re
import stat
from typing import TypedDict
from urllib.parse import quote

from .checkpoint_schema import CheckpointValue
from .status import _workspace_status_snapshot, _WorkspaceStatusSnapshot
from .transcript_segments import load_transcript_segments
from .workspace import Workspace, load_json


class WorkspaceMeta(TypedDict):
    tags: list[str]
    name: str
    title: str
    duration: float
    mode_head: str
    readiness: str
    cps: int
    head: str
    labels: list[str]
    has_log: bool
    speech_ratio: float
    scenes: int | None
    highlights: int | None


# 사람 표시 계층 용어 맵 — 영상업계 어휘로 치환(기계 상태값·원장은 불변).
STATUS_KO = {"verified": "확인", "corrected": "정정", "hypothesized": "추정"}
READINESS_KO = {"converged": "완결", "cover_gaps": "공백 남음",
                "verify_more": "검증 더"}
_REASON_KO = [
    ("keyframes-budget", "대표 컷 선별"),
    ("gap-verify", "공백 구간 확인"),
    ("burst", "오디오 피크 확인"),
    ("filmstrip-overview", "조망 시트"),
    ("sharp-gate", "선명도 선별"),
    ("ocr-scan", "자막·화면문자 스캔"),
    ("ocr-frame", "화면문자 판독"),
    ("faces-count", "출연 확인"),
    ("endcard", "엔딩 확인"),
    ("scene-change", "컷 전환 확인"),
]


def human_reason(reason: str | None) -> str:
    """촬영 사유를 업계어와 정확한 원문으로 함께 표시한다."""
    if not reason:
        return ""
    for prefix, ko in _REASON_KO:
        if reason.startswith(prefix):
            return f"{ko} ({reason})"
    return reason


def escape_markdown_text(value: object) -> str:
    """Render an exact value as inert, single-line Markdown text."""
    visible = (
        str(value)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", r"\n")
    )
    escaped = html.escape(visible, quote=False)
    for marker in ("\\", "`", "*", "_", "[", "]", "|", "~"):
        escaped = escaped.replace(marker, f"\\{marker}")
    return escaped


def escape_markdown_alt_text(value: object) -> str:
    """Render an exact value inside Markdown image alt text."""
    return (
        escape_markdown_text(value)
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


NARRATIVE_START = "<!-- tca:narrative:start -->"
NARRATIVE_END = "<!-- tca:narrative:end -->"
_NARRATIVE_RE = re.compile(
    re.escape(NARRATIVE_START) + r"(.*?)" + re.escape(NARRATIVE_END),
    re.DOTALL,
)
_NARRATIVE_TEMPLATE = """
## 내용
(1~3줄 개요 — 에이전트 작성, 재생성에도 보존)

## 전개
(장면 흐름 서술)

## 기승전결
- 기(도입):
- 승(전개):
- 전(전환):
- 결(마무리):
"""


def tagfmt(tag: str) -> str:
    """Return an Obsidian-safe tag without whitespace."""
    return re.sub(r"\s+", "-", tag.strip())


def checkpoint_fragment(checkpoint_id: str) -> str:
    """Return the URL-encoded link fragment for one checkpoint ID."""
    return quote(checkpoint_id, safe="")


def md_target(target: str) -> str:
    """Return a structurally inert CommonMark/Obsidian link destination.

    Unicode spelling is preserved exactly so NFC and NFD filenames both keep
    resolving. Bytes that can terminate an angle destination, introduce a
    second fragment, or inject a physical line are percent-encoded. Fragments
    may already contain ``checkpoint_fragment`` escapes; valid ``%HH`` escapes
    are retained while stray percent signs are encoded.
    """

    path, separator, fragment = target.partition("#")

    def encode_path(value: str) -> str:
        encoded: list[str] = []
        for character in value:
            if (
                character in {"%", "<", ">", "\\", "#"}
                or ord(character) < 0x20
                or ord(character) == 0x7F
            ):
                encoded.extend(
                    f"%{byte:02X}" for byte in character.encode("utf-8")
                )
            else:
                encoded.append(character)
        return "".join(encoded)

    safe_path = encode_path(path)
    if separator:
        safe_fragment = re.sub(
            r"%(?![0-9A-Fa-f]{2})",
            "%25",
            fragment,
        )
        safe_fragment = quote(safe_fragment, safe="%=-._~:")
        safe_target = f"{safe_path}#{safe_fragment}"
    else:
        safe_target = safe_path
    return (
        f"<{safe_target}>"
        if any(character in safe_target for character in " ()")
        else safe_target
    )


def checkpoint_anchor(checkpoint_id: str) -> str:
    """Return the checkpoint ID as one inert HTML ``id`` attribute value."""
    visible = (
        html.escape(checkpoint_id, quote=True)
        .replace("\r", "&#13;")
        .replace("\n", "&#10;")
        .replace("\t", "&#9;")
    )
    return visible


def speaker_labels(value: CheckpointValue | None) -> list[str]:
    """Flatten persisted speaker/entity values to canonical labels."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        label = value.get("label")
        return [label] if isinstance(label, str) and label else []
    if isinstance(value, (list, tuple)):
        return [label for item in value for label in speaker_labels(item)]
    return []


# 저장 표면과 읽는 표면을 가른다. 원장에는 에이전트가 쓴 말이 그대로 남아야
# 인용의 정본이 되고 검색이 그 위에서 돈다 — 다만 Obsidian에서 사람이 읽는
# 문서에까지 도구 어휘가 나오면 비개발자는 문장을 해석하지 못한다.
# 실측(사람 표시 필드 973건): OCR 108 · 버스트 81 · 스트립 23 · score 16 ·
# full-res 6. 오탐이 날 만큼 짧거나 흔한 말(셀·프레임)은 손대지 않는다.
_SURFACE_WORDING: tuple[tuple[str, str], ...] = (
    ("오디오 버스트", "소리 급상승"),
    ("full-res", "고화질"),
    ("filmstrip", "미리보기 시트"),
    ("스트립 셀", "미리보기 칸"),
    ("스트립 스캔", "미리보기 훑기"),
    ("스트립", "미리보기 시트"),
    ("버스트", "소리 급상승"),
    ("burst", "소리 급상승"),
    ("스팬", "구간"),
    ("span", "구간"),
    ("score", "세기"),
    ("OCR", "화면 글자"),
    ("ASR", "자동 자막"),
    ("정합", "일치"),
)


def humanize_surface(text: str) -> str:
    """읽는 표면용 어휘로 바꾼다 — 수치·시각은 건드리지 않는다.

    긴 표현을 먼저 치환해 "오디오 버스트"가 "오디오 소리 급상승"이 되는 일을
    막는다. 대체어에는 원래 키가 들어 있지 않아 두 번 적용해도 결과가 같다.
    """
    if not text:
        return text
    for tool_word, everyday in _SURFACE_WORDING:
        text = text.replace(tool_word, everyday)
    return text


def relation_triples(checkpoint: dict) -> list[dict]:
    """체크포인트에 기재된 유효한 관계(s·p·o)만.

    "관계가 기재되었는가"의 판정은 한 곳에서만 산다 — 위키 생성기와 감사가
    기준을 따로 들면, 위키가 세는 관계와 감사가 "비었다"고 보는 관계가
    갈라져 후보 목록이 조용히 틀어진다.
    """
    return [
        relation for relation in checkpoint.get("relations") or []
        if isinstance(relation, dict)
        and relation.get("s") and relation.get("p") and relation.get("o")
    ]


def string_values(value: CheckpointValue | None) -> list[str]:
    """Return only direct string values from a scalar or sequence field."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


def narrative_block(ws: Workspace) -> str:
    """Return agent-authored narrative while preserving regeneration edits."""
    candidates = [
        ws.root / f"{ws.doc_stem}.md",
        # 개명 이전 이름들 — 에이전트가 쓴 서사는 파일명이 바뀐다고
        # 사라지면 안 된다(제목 기반 개명·그 이전 scene-log.md 둘 다).
        ws.root / f"{ws.root.name}.md",
        ws.root / "scene-log.md",
    ]
    log_path = None
    for candidate in candidates:
        try:
            mode = candidate.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(mode):
            raise OSError(
                f"unsafe scene-log source (not a regular file): {candidate}"
            )
        log_path = candidate
        break
    if log_path is None:
        return _NARRATIVE_TEMPLATE
    # Missing and unreadable are different states. Once an authored projection
    # exists, an I/O failure must stop regeneration rather than silently
    # replacing its prose with the placeholder template.
    text = log_path.read_text(encoding="utf-8")
    match = _NARRATIVE_RE.search(text)
    return match.group(1) if match else _NARRATIVE_TEMPLATE


def narrative_head(ws: Workspace) -> str | None:
    """Return the first filled line of the video-level content section."""
    section = re.search(r"## 내용\n(.*?)(?:\n##|\Z)", narrative_block(ws), re.DOTALL)
    if section is None:
        return None
    for line in section.group(1).strip().splitlines():
        line = line.strip()
        if line and not line.startswith("("):
            return line
    return None


def ws_meta(
    ws: Workspace,
    *,
    status_snapshot: _WorkspaceStatusSnapshot | None = None,
) -> WorkspaceMeta:
    """Project one workspace into the shared canonical corpus summary."""
    from .brief import recommend_mode

    manifest = ws.manifest
    segments = load_transcript_segments(ws.transcript_path)
    speech = sum(segment["end"] - segment["start"] for segment in segments)
    scenes = load_json(ws.root / "scenes.json")
    highlights = load_json(ws.root / "highlights.json")
    mode, _ = recommend_mode(
        manifest,
        segments,
        speech,
        scenes,
        highlights,
        load_json(ws.root / "ocr_transcript.json"),
    )
    snapshot = status_snapshot or _workspace_status_snapshot(ws)
    promoted = snapshot.supported_checkpoints
    status = snapshot.status
    checkpoint_count = sum(status["counts"].values())
    head = narrative_head(ws) or next(
        (
            checkpoint.get("situation") or checkpoint["hypothesis"]
            for checkpoint in promoted
        ),
        "검증 대기" if checkpoint_count else "—",
    )
    labels = sorted(
        {
            label
            for checkpoint in promoted
            for label in speaker_labels(checkpoint.get("speakers"))
            + speaker_labels(checkpoint.get("entities"))
        }
    )
    tags = sorted(
        {
            tagfmt(tag)
            for checkpoint in promoted
            for tag in string_values(checkpoint.get("tags"))
            if tag.strip()
        }
    )
    duration = float(manifest["duration"])
    return {
        "tags": tags,
        "name": ws.root.name,
        # 사람 표시 계층: URL ingest가 저장한 원제목 우선, 없으면 디렉터리명.
        # 기계 키(name)는 링크·캐시 정본으로 불변 — 가독성은 표시명이 담당.
        "title": str(manifest.get("title") or ws.root.name).strip(),
        "duration": duration,
        "mode_head": mode.split(" — ")[0],
        "readiness": status["readiness"]["status"],
        "cps": checkpoint_count,
        "head": str(head)[:80],
        "labels": labels,
        "has_log": bool(checkpoint_count),
        "speech_ratio": speech / duration if duration else 0.0,
        "scenes": len(scenes) if scenes is not None else None,
        "highlights": len(highlights) if highlights is not None else None,
    }
