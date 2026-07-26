"""Obsidian-friendly per-workspace scene-log projection."""

from __future__ import annotations

import json
from urllib.parse import quote

from .checkpoint_selection import selected_checkpoints
from .corpus_projection import (
    NARRATIVE_END,
    NARRATIVE_START,
    READINESS_KO,
    STATUS_KO,
    WorkspaceMeta,
    checkpoint_anchor,
    escape_markdown_alt_text,
    escape_markdown_text,
    humanize_surface,
    md_target,
    narrative_block,
    speaker_labels,
    ws_meta,
)
from .image_index import (
    checkpoint_image_backlinks,
    image_counts,
    image_record_map,
)
from .image_provenance import image_identity, load_image_records, resolve_image_path
from .image_types import ImageRecord
from .sequence import load_sequences
from .timestamps import fmt_ts, fmt_ts_compact
from .workspace import Workspace


def _yaml(value: object) -> str:
    """One YAML-safe scalar (JSON strings are valid YAML flow scalars)."""
    return (
        json.dumps(value, ensure_ascii=False)
        if isinstance(value, str)
        else str(value)
    )


def _path_provenance(value: str) -> str:
    """Keep ordinary paths readable while containing structural path text."""
    if not any(character in value for character in "`\r\n"):
        return f"`{value}`"
    return f"<code>{escape_markdown_text(value)}</code>"


def export_md(
    ws: Workspace,
    ids: list[str] | None = None,
    *,
    image_records: list[ImageRecord] | None = None,
    index_backlink: str = "../INDEX.md",
    meta: WorkspaceMeta | None = None,
) -> str:
    """Render scene hierarchy, checkpoints, and causal image backlinks.

    meta는 호출자가 이미 계산한 ws_meta를 재사용하는 통로다 — build_index가
    행 구성에 쓴 것을 넘겨 워크스페이스당 이중 계산(전사 재적재·mode 추천·
    status 스냅샷)을 없앤다(codex/grounded-wiki-promotion d44bf49 재구현).
    """
    meta = ws_meta(ws) if meta is None else meta
    records = load_image_records(ws) if image_records is None else image_records
    backlinks = checkpoint_image_backlinks(ws)
    counts = image_counts(records, backlinks)
    lines = [
        "---",
        "type: tca-scene-log",
        # Obsidian 퀵스위처에서 동명 scene-log 25+개를 구분하는 사람 이름
        f"aliases: [{_yaml(meta['title'] + ' — 장면 로그')}]",
        f"video: {_yaml(ws.video.name)}",
        f"duration_s: {meta['duration']:.1f}",
        f"duration_display: {_yaml(fmt_ts_compact(meta['duration']))}",
        f"mode: {_yaml(meta['mode_head'])}",
        f"readiness: {meta['readiness']}",
        f"checkpoints: {meta['cps']}",
        f"images: {counts['images']}",
        f"causal_edges: {counts['causal_edges']}",
        f"unknown_inventory_links: {counts['unknown_inventory_links']}",
        f"checkpoint_linked_images: {counts['checkpoint_linked_images']}",
    ]
    # 같은 타입=같은 키 집합(빈 값은 빈 리스트로 명시) — 키 부재와 빈 값이
    # 다르게 동작하는 Obsidian Bases/Dataview 필터의 스키마 드리프트 방지
    keywords = sorted(set(meta["tags"]) | set(meta["labels"][:6]))[:10]
    for key, values in (("entities", meta["labels"]),
                        ("tags", meta["tags"]),
                        ("keywords", keywords)):
        if values:
            lines.append(f"{key}:")
            lines += [f"  - {_yaml(value)}" for value in values]
        else:
            lines.append(f"{key}: []")

    selected = selected_checkpoints(ws, ids)
    arcs = [checkpoint.get("arc") for checkpoint in selected if checkpoint.get("arc")]
    three = [
        f"- **화면**: 컷 전환 {meta['scenes']}회 · 유형 {meta['mode_head']}"
        if meta["scenes"] is not None
        else f"- **화면**: 유형 {meta['mode_head']} (컷 전환 미계산)",
        f"- **소리**: 발화 비율 {meta['speech_ratio']:.0%}"
        + (
            f" · 오디오 피크 {meta['highlights']}곳"
            if meta["highlights"] is not None
            else ""
        ),
        f"- **서사**: 장면 구간 {meta['cps']}개 · "
        f"{READINESS_KO.get(meta['readiness'], meta['readiness'])}"
        + (
            f" · 기승전결 라벨 {len(arcs)}/{len(selected)}"
            if arcs
            else " · 기승전결 미라벨"
        ),
    ]
    lines += [
        "---",
        "",
        f"# 장면 로그: {escape_markdown_text(ws.video.name)}",
        "",
        f"[← 코퍼스 인덱스]({md_target(index_backlink)})",
        *(
            [
                    f"[스틸·조망 목록]"
                    f"({md_target(f'{ws.doc_stem}-images.md')}) · "
                    f"스틸·시트 {counts['images']}장 · 촬영 기록 "
                    f"{counts['causal_edges']}건 · 출처 미기록 "
                    f"{counts['unknown_inventory_links']}건"
            ]
            if records
            else []
        ),
        *(
            [f"[편집안]({md_target(f'{ws.doc_stem}-edits.md')}) · "
             f"편집안 {len(edit_sequences)}안"]
            if (edit_sequences := load_sequences(ws))
            else []
        ),
        "",
        NARRATIVE_START + narrative_block(ws) + NARRATIVE_END,
        "",
        "## 3요소 신호 (화면 · 소리 · 서사)",
        "",
        *three,
        "",
        "## 장면 구간",
        "",
        (
            "| id | 구간 | 기승전결 | 상태 | 상황 | 화자 | 확신도 |"
            if arcs
            else "| id | 구간 | 상태 | 상황 | 화자 | 확신도 |"
        ),
        (
            "|---|---|---|---|---|---|---|"
            if arcs
            else "|---|---|---|---|---|---|"
        ),
    ]
    record_by_path = image_record_map(records)
    try:
        video_relative = (
            ws.video.relative_to(ws.root) if ws.video.is_file() else None
        )
    except ValueError:
        video_relative = None
    for checkpoint in selected:
        start, end = checkpoint["span"]
        checkpoint_id = str(checkpoint["id"])
        anchor = checkpoint_anchor(checkpoint_id)
        shown_id = escape_markdown_text(checkpoint_id)
        speakers = escape_markdown_text(
            ", ".join(speaker_labels(checkpoint.get("speakers"))) or "-"
        )
        confidence = checkpoint.get("confidence")
        # 내용은 hypothesis가 정본이다. situation은 태그성 라벨이라 그것으로
        # 덮으면 verified 승격이 오히려 정보를 잃는다 — 확인된 구간이 추정
        # 구간보다 빈약해 보이는 역전이 실제로 관측됐다(2026-07-25).
        situation = escape_markdown_text(humanize_surface(str(
            checkpoint.get("hypothesis") or checkpoint.get("situation") or ""
        )))
        span_text = f"{fmt_ts(start)}–{fmt_ts(end)}"
        if video_relative is not None:
            target = md_target(
                f"{video_relative.as_posix()}#t={start:.1f}"
            )
            if not target.startswith("<"):
                target = f"<{target}>"
            span_text = f"[{span_text}]({target})"
        arc_cell = (
            f"| {escape_markdown_text(checkpoint.get('arc', '-'))} "
            if arcs
            else ""
        )
        shown_status = escape_markdown_text(
            STATUS_KO.get(checkpoint["status"], checkpoint["status"])
        )
        shown_confidence = escape_markdown_text(
            confidence if confidence is not None else "-"
        )
        lines.append(
            f'| <a id="{anchor}"></a>{shown_id} | '
            f"{span_text} {arc_cell}"
            f"| {shown_status} "
            f"| {situation} | {speakers} "
            f"| {shown_confidence} |"
        )

    evidence_lines: list[str] = []
    for checkpoint in selected:
        evidence = checkpoint.get("visual_evidence") or []
        if isinstance(evidence, str):
            evidence_paths = [evidence]
        elif isinstance(evidence, list):
            evidence_paths = evidence
        else:
            continue
        evidence_paths = [
            relative for relative in evidence_paths
            if isinstance(relative, str)
        ]
        if not evidence_paths:
            continue
        evidence_context = humanize_surface(str(
            checkpoint.get("situation") or checkpoint["hypothesis"]
        ))
        shown_checkpoint_id = escape_markdown_text(checkpoint["id"])
        evidence_lines.append(
            f"### {shown_checkpoint_id} — "
            f"{escape_markdown_text(evidence_context)}"
        )
        # 프레임을 열어 무엇을 읽었는지가 이 프레임 묶음의 재사용 가치다.
        # 같은 체크포인트 판독을 스틸 수만큼 반복하면 문맥만 부풀어 오른다.
        described = humanize_surface(str(checkpoint.get("hypothesis") or ""))
        evidence_lines.append(
            f"- 장면 설명: {escape_markdown_text(described)}"
        )
        reading = humanize_surface(str(checkpoint.get("note") or "").strip())
        if reading:
            evidence_lines.append(
                "- 확인·재활용 지식: "
                f"{escape_markdown_text(reading)}"
            )
        for relative in evidence_paths:
            try:
                image_path, resolved = resolve_image_path(ws, relative)
            except ValueError:
                image_path = ""
                resolved = None
            record = record_by_path.get(image_path)
            if record is None and image_path:
                record = record_by_path.get(image_identity(ws, image_path))
            if record:
                evidence_lines.append(
                    "- 스틸: [스틸 상세]"
                    f"({md_target(f'{ws.doc_stem}-images.md'
                                  f'#{record['image_id']}')})"
                )
            if resolved is not None and resolved.is_file():
                shown_context = escape_markdown_alt_text(humanize_surface(str(
                    checkpoint.get("situation") or checkpoint["hypothesis"]
                )))
                shown_alt_id = escape_markdown_alt_text(checkpoint["id"])
                evidence_lines.append(
                    f"![{shown_alt_id}"
                    f"{' · ' + shown_context if shown_context else ''}]"
                    f"({quote(image_path, safe='/-._~')})"
                )
            elif not image_path:
                evidence_lines.append(
                    "- 유효하지 않은 이미지 경로: "
                    f"{_path_provenance(relative)}"
                )
            else:
                evidence_lines.append(
                    f"- 파일 없음: {_path_provenance(image_path)}"
                )
        evidence_lines.append("")
    if evidence_lines:
        lines += ["", "## 근거 스틸", ""] + evidence_lines
    return "\n".join(lines) + "\n"
