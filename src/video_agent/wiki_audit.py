"""Read-only audits for the generated video wiki layer."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

from .wiki_state import NOTES_END, NOTES_PLACEHOLDER, NOTES_START


class WikiLayerAudit(TypedDict):
    """지식계층(비디오 위키) 결정적 위생 지표 — 패턴 불변식 감사(#31/#35)."""

    corpus_index_bytes: int
    corpus_index_over_limit: bool
    entities: int
    durable_entities: int
    candidate_entities: int
    relations: int
    quotes: int
    notes_filled: int
    alias_groups: int


class WikiIntegrityAudit(TypedDict):
    """지식 그래프 무결성 — 깨진 md 링크·인덱스 미도달(고아) 페이지."""

    broken_links: list[str]
    orphan_entities: list[str]


class ImprovementCandidates(TypedDict):
    """실행 가능한 개선 큐 — 수치가 아니라 다음 큐레이션 작업 목록."""

    notes_missing_durable: list[str]
    relations_missing_recurring: list[str]
    narrative_missing_converged: list[str]


WIKI_INDEX_BYTE_LIMIT = 10 * 1024  # 라우팅 인덱스 상한 — 초과=스펙화 조기경보

_WIKI_COUNTS_RE = re.compile(
    r"엔티티 (\d+) · 태그 \d+ · 관계 (\d+) · 대사 (\d+)")
_NOTES_BODY_RE = re.compile(
    re.escape(NOTES_START) + r"(.*?)" + re.escape(NOTES_END), re.DOTALL)
_DURABLE_BULLET_RE = re.compile(r"^- \[([^\]]+)\]\(entities/", re.MULTILINE)
_APPEARANCES_RE = re.compile(r"^appearances: (\d+)$", re.MULTILINE)


def _section_bullets(text: str, heading: str) -> int:
    section = re.search(
        re.escape(heading) + r".*?\n(.*?)(?:\n## |\Z)", text, re.DOTALL)
    if section is None:
        return 0
    return sum(1 for line in section.group(1).splitlines()
               if line.startswith("- "))


def audit_wiki_layer(root: Path) -> WikiLayerAudit | None:
    """위키 인덱스·엔티티 페이지를 읽기 전용으로 계측한다 (생성 포맷 전제)."""
    index = root / "wiki" / "INDEX.md"
    if not index.is_file():
        return None
    text = index.read_text(encoding="utf-8")
    counts = _WIKI_COUNTS_RE.search(text)
    corpus_index = root / "INDEX.md"
    notes_filled = 0
    ent_dir = root / "wiki" / "entities"
    if ent_dir.is_dir():
        placeholder = NOTES_PLACEHOLDER.strip()
        for page in ent_dir.glob("*.md"):
            m = _NOTES_BODY_RE.search(page.read_text(encoding="utf-8"))
            body = (m.group(1).strip() if m else "")
            if body and body != placeholder:
                notes_filled += 1
    index_bytes = corpus_index.stat().st_size if corpus_index.is_file() else 0
    return {
        "corpus_index_bytes": index_bytes,
        "corpus_index_over_limit": index_bytes > WIKI_INDEX_BYTE_LIMIT,
        "entities": int(counts.group(1)) if counts else 0,
        "durable_entities": _section_bullets(text, "## 엔티티\n"),
        "candidate_entities": _section_bullets(text, "## 엔티티 후보"),
        "relations": int(counts.group(2)) if counts else 0,
        "quotes": int(counts.group(3)) if counts else 0,
        "notes_filled": notes_filled,
        "alias_groups": _section_bullets(text, "## 통합 후보"),
    }


def _link_destinations(text: str) -> list[str]:
    """CommonMark 링크 목적지 추출 — 균형 괄호 경로(엔티티 라벨) 지원."""
    out = []
    i = 0
    while (i := text.find("](", i)) != -1:
        depth, j = 1, i + 2
        while j < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            j += 1
        if depth == 0:
            out.append(text[i + 2:j - 1])
        i = j
    return out


def _md_targets(path: Path, text: str) -> list[tuple[str, Path | None]]:
    """지식 계층(md) 링크만 해석해 (원본 표기, 절대 경로) 목록으로.

    앵글브래킷(<...>) 목적지는 껍질을 벗겨 해석한다. 감싸지 않은 원시
    공백 목적지는 Obsidian이 해석하지 못하는데 이 검사기는 관대하게
    통과시켜 왔다(검사기-소비자 격차, 46건 실측) — None으로 돌려 깨진
    링크로 계상한다.
    """
    out: list[tuple[str, Path | None]] = []
    for raw in _link_destinations(text):
        target = raw.strip()
        wrapped = target.startswith("<") and target.endswith(">")
        if wrapped:
            target = target[1:-1]
        target = target.split("#", 1)[0].strip()
        if not target or "://" in target or not target.endswith(".md"):
            continue
        if not wrapped and " " in target:
            out.append((raw, None))
            continue
        out.append((raw, (path.parent / target).resolve()))
    return out


def audit_wiki_integrity(root: Path) -> WikiIntegrityAudit:
    """깨진 링크와 INDEX에서 BFS 미도달 엔티티 페이지를 검출한다."""
    index = root / "INDEX.md"
    pages = [p for p in dict.fromkeys([
                         index,
                         *sorted(root.glob("*/*.md")),
                         *sorted(root.glob("*/*/*.md")),
                         *sorted(root.glob("*/*/*/*.md"))])
             if p.is_file()]
    broken: list[str] = []
    edges: dict[Path, list[Path]] = {}
    for page in pages:
        text = page.read_text(encoding="utf-8")
        resolved = []
        for raw, target in _md_targets(page, text):
            if target is not None and target.is_file():
                resolved.append(target)
            elif target is None:
                broken.append(f"{page.relative_to(root)} → {raw} "
                              "(원시 공백 — Obsidian 비해석)")
            else:
                broken.append(f"{page.relative_to(root)} → {raw}")
        edges[page.resolve()] = resolved
    reachable: set[Path] = set()
    queue = [index.resolve()] if index.is_file() else []
    while queue:
        page = queue.pop()
        if page in reachable:
            continue
        reachable.add(page)
        queue += edges.get(page, [])
    orphans = [
        str(p.relative_to(root))
        for p in sorted(root.glob("wiki/entities/*.md"))
        if p.resolve() not in reachable
    ]
    return {"broken_links": broken, "orphan_entities": orphans}


def collect_improvements(
    root: Path, narrative_missing: list[str]
) -> ImprovementCandidates:
    """결정적 개선 큐 — 정착인데 서술 없음·다회 등장인데 관계 없음 등."""
    index = root / "wiki" / "INDEX.md"
    notes_missing: list[str] = []
    relations_missing: list[str] = []
    if index.is_file():
        text = index.read_text(encoding="utf-8")
        section = re.search(r"## 엔티티\n(.*?)(?:\n## |\Z)", text, re.DOTALL)
        durable = (_DURABLE_BULLET_RE.findall(section.group(0))
                   if section else [])
        placeholder = NOTES_PLACEHOLDER.strip()
        for label in durable:
            page = root / "wiki" / "entities" / f"{label}.md"
            if not page.is_file():
                continue
            page_text = page.read_text(encoding="utf-8")
            m = _NOTES_BODY_RE.search(page_text)
            body = (m.group(1).strip() if m else "")
            if not body or body == placeholder:
                notes_missing.append(label)
            appearances = _APPEARANCES_RE.search(page_text)
            if (appearances and int(appearances.group(1)) >= 3
                    and "## 관계" not in page_text):
                relations_missing.append(label)
    return {
        "notes_missing_durable": notes_missing,
        "relations_missing_recurring": relations_missing,
        "narrative_missing_converged": narrative_missing,
    }
