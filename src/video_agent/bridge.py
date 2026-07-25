"""Wiki bridge — publish routing stubs into an external knowledge vault.

정본은 va-out(독립 vault)이다. 외부 브레인 vault에는 라우팅 스텁+백링크만
발행한다(병합·중첩 금지, ADR-0002). 재발행은 멱등이며, 이 모듈이 소유한
스텁(frontmatter `type: tca-bridge-stub`)만 갱신·제거하고 vault의 다른
파일은 절대 건드리지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

from .corpus_projection import ws_meta
from .search import find_workspaces
from .timestamps import fmt_ts_compact
from .workspace import Workspace

BRIDGE_DIRNAME = "tca-bridge"
STUB_TYPE = "tca-bridge-stub"
INDEX_STUB_NAME = "tca-bridge.md"
_MARKER = f"type: {STUB_TYPE}"


class BridgeReport(TypedDict):
    published: int
    removed: int
    foreign: int
    dest: str


def _aliases(title: str, labels: list[str]) -> str:
    seen: list[str] = []
    for alias in [title, *labels]:
        alias = alias.strip()
        if alias and alias not in seen:
            seen.append(alias)
    return json.dumps(seen, ensure_ascii=False)


def _ws_stub(meta: dict, ws_path: Path, corpus: Path) -> str:
    title = str(meta["title"])
    head = str(meta["head"]).strip() or "—"
    duration = fmt_ts_compact(float(meta["duration"] or 0.0))
    lines = [
        "---",
        f"type: {STUB_TYPE}",
        f"aliases: {_aliases(title, list(meta['labels']))}",
        "---",
        "",
        f"# {title}",
        "",
        head,
        "",
        f"- 모드 {meta['mode_head']} · {duration} · readiness "
        f"{meta['readiness']} · 체크포인트 {meta['cps']}",
        f"- 정본 원장: `{ws_path.resolve()}` — 이 스텁은 라우팅 전용, "
        "여기서 내용을 편집·병합하지 않는다",
        f"- 코퍼스 인덱스: `{(corpus / 'INDEX.md').resolve()}`",
        f"- 질의 재개: `va search \"<텀>\"` → `va brief {ws_path.resolve()}`",
        "",
    ]
    return "\n".join(lines)


def _index_stub(rows: list[tuple[str, str, str]], corpus: Path) -> str:
    lines = [
        "---",
        f"type: {STUB_TYPE}",
        'aliases: ["TIMECODE 비디오 기억", "영상 기억"]',
        "---",
        "",
        "# TIMECODE 비디오 기억 — 라우팅 스텁",
        "",
        f"정본은 `{corpus.resolve()}` (독립 vault, `INDEX.md`가 지도)이다. "
        "이 폴더는 라우팅 전용 — 내용 병합·중첩 금지(ADR-0002).",
        "",
        "| 영상 | 요약 | 스텁 |",
        "|---|---|---|",
    ]
    for name, title, head in rows:
        summary = head.replace("|", "\\|").replace("\n", " ")[:60]
        lines.append(f"| {title} | {summary} | [[{name}]] |")
    lines.append("")
    return "\n".join(lines)


def _is_owned(path: Path) -> bool:
    try:
        return _MARKER in path.read_text(encoding="utf-8")[:200]
    except OSError:
        return False


def _stub_name(ws_path: Path, corpus: Path) -> str:
    """코퍼스 상대경로가 스텁 정체성 — 그룹 하위 동명 leaf 충돌 방지."""
    try:
        parts = ws_path.relative_to(corpus.resolve()).parts
    except ValueError:
        parts = (ws_path.name,)
    name = "-".join(parts)
    if f"{name}.md" == INDEX_STUB_NAME:
        name = f"{name}-ws"
    return name


def publish_bridge(
    corpus: Path,
    vault: Path,
    *,
    workspace_paths: Sequence[Path] | None = None,
) -> BridgeReport:
    """Publish per-workspace routing stubs plus one corpus index stub."""
    vault = Path(vault)
    if not vault.is_dir():
        raise ValueError(f"vault 디렉터리가 없습니다: {vault}")
    corpus = Path(corpus)
    discovered = (
        list(workspace_paths)
        if workspace_paths is not None
        else find_workspaces([str(corpus)])
    )
    paths = sorted({path.resolve() for path in discovered}, key=str)
    if not paths:
        raise ValueError(f"manifest-backed 워크스페이스가 없습니다: {corpus}")

    dest = vault / BRIDGE_DIRNAME
    dest.mkdir(exist_ok=True)

    expected: dict[str, str] = {}
    rows: list[tuple[str, str, str]] = []
    for path in paths:
        meta = ws_meta(Workspace.load(path))
        stem = _stub_name(path, corpus)
        if f"{stem}.md" in expected:
            raise ValueError(f"스텁 이름 충돌: {stem} — 코퍼스 구조 확인 필요")
        expected[f"{stem}.md"] = _ws_stub(dict(meta), path, corpus)
        rows.append((stem, str(meta["title"]), str(meta["head"])))
    expected[INDEX_STUB_NAME] = _index_stub(rows, corpus)

    # 보존 계약 선검사: 어떤 파일도 지우거나 쓰기 전에, 발행 대상과 동명인
    # 비소유(사람) 파일이 있으면 전체 발행을 거부한다 — 부분 변형 금지.
    conflicts = [
        str(dest / name)
        for name in expected
        if (dest / name).is_file() and not _is_owned(dest / name)
    ]
    if conflicts:
        raise ValueError(
            "외부 파일과 스텁 이름 충돌 — 덮어쓰지 않습니다: "
            + ", ".join(conflicts)
        )

    removed = 0
    foreign = 0
    for existing in sorted(dest.glob("*.md")):
        if existing.name in expected:
            continue
        if _is_owned(existing):
            existing.unlink()
            removed += 1
        else:
            foreign += 1

    for name, content in expected.items():
        target = dest / name
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8")

    return {
        "published": len(expected),
        "removed": removed,
        "foreign": foreign,
        "dest": str(dest),
    }
