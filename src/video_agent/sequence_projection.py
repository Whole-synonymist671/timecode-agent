"""Human and NLE-facing projections of the edit-decision ledger."""

from __future__ import annotations

import json

from .checkpoints import load_checkpoints
from .corpus_projection import checkpoint_fragment, md_target
from .sequence_grounding import checkpoint_revision_hash, revision_drift
from .sequence_schema import SEQ_STATUS_KO
from .sequence_store import load_sequences
from .timestamps import fmt_ts_compact
from .workspace import Workspace


def sequence_cut_selection(
    ws: Workspace, sequence_id: str
) -> list[dict]:
    """Convert a sequence into the checkpoint-compatible export shape."""
    sequences = {sequence["id"]: sequence for sequence in load_sequences(ws)}
    if sequence_id not in sequences:
        raise ValueError(
            f"unknown sequence id: {sequence_id}"
            f" (있는 것: {sorted(sequences)})"
        )
    sequence = sequences[sequence_id]
    pins = sequence.get("checkpoint_revisions") or {}
    checkpoints_by_id = {
        str(checkpoint.get("id")): checkpoint
        for checkpoint in load_checkpoints(ws)
    }
    selection: list[dict] = []
    for cut in sequence["cuts"]:
        note = (
            cut.get("note")
            or cut.get("role")
            or str(sequence.get("intent") or "")
        )
        checkpoint_ids = list(cut.get("checkpoint_ids") or [])
        selection.append(
            {
                "id": f"{sequence['id']}-{cut['order']:02d}",
                "span": cut["span"],
                "status": "cut",
                "hypothesis": note,
                "situation": note,
                "sequence_id": sequence["id"],
                "order": cut["order"],
                "checkpoint_ids": checkpoint_ids,
                # 터미널 승격이 고정한 근거 리비전 — 인계 산출물이 원장
                # 없이도 "어느 판정 내용에 근거했는가"를 증명하는 유일 통로.
                "checkpoint_revisions": {
                    checkpoint_id: pins[checkpoint_id]
                    for checkpoint_id in checkpoint_ids
                    if checkpoint_id in pins
                },
                # 내보내기 시점의 판정 상태 — 핀(내용 증명)과 상보:
                # NLE 쪽이 원장 없이 verified/신뢰도로 색·필터링한다.
                # drifted=핀 이후 판정 내용이 갱신됨 — 현행 상태를 핀된
                # 근거의 검증으로 오독하지 않도록 인계 자체에 표시한다.
                "checkpoint_states": {
                    checkpoint_id: {
                        "status": checkpoints_by_id[checkpoint_id].get(
                            "status"
                        ),
                        "confidence": checkpoints_by_id[checkpoint_id].get(
                            "confidence"
                        ),
                        "drifted": (
                            checkpoint_id in pins
                            and checkpoint_revision_hash(
                                checkpoints_by_id[checkpoint_id]
                            ) != pins[checkpoint_id]
                        ),
                    }
                    for checkpoint_id in checkpoint_ids
                    if checkpoint_id in checkpoints_by_id
                },
                "signals": list(cut.get("signals") or []),
            }
        )
    return selection


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def export_edits_md(
    ws: Workspace, *, index_backlink: str = "../INDEX.md"
) -> str:
    """Project edit decisions into a human-readable Markdown surface."""
    sequences = sorted(load_sequences(ws), key=lambda sequence: sequence["id"])
    title = str(ws.manifest.get("title") or ws.root.name).strip()
    log_name = f"{ws.doc_stem}.md"
    checkpoints = load_checkpoints(ws)
    checkpoints_by_id = {
        str(checkpoint["id"]): checkpoint for checkpoint in checkpoints
    }
    has_log = bool(checkpoints)
    lines = [
        "---",
        "type: tca-edit-log",
        f"aliases: [{json.dumps(title + ' — 편집안', ensure_ascii=False)}]",
        f"sequences: {len(sequences)}",
        "---",
        "",
        f"# 편집안: {ws.video.name}",
        "",
        f"[← 코퍼스 인덱스]({index_backlink})"
        + (f" · [장면 로그]({md_target(log_name)})" if has_log else ""),
        "",
        "편집 결정 원장의 사람 표면입니다. 컷의 근거는 장면 구간으로 "
        "역추적됩니다 — 사실 판정은 장면 로그가 정본이며 편집안은 "
        "사실을 바꾸지 않습니다.",
    ]
    for sequence in sequences:
        status = SEQ_STATUS_KO.get(
            sequence["status"], sequence["status"]
        )
        lines += [
            "",
            f"## {sequence['id']} — "
            f"{_cell(sequence.get('intent') or '')} ({status})",
            "",
        ]
        pins = sequence.get("checkpoint_revisions") or {}
        drifted = revision_drift(sequence, checkpoints_by_id)
        if drifted:
            lines += [
                "> ⚠ 근거 리비전 드리프트: "
                + ", ".join(_cell(checkpoint_id) for checkpoint_id in drifted)
                + " — 승격 이후 장면 판정이 갱신되었습니다. "
                "경계를 재검증한 뒤 재승격하십시오.",
                "",
            ]
        brief = sequence.get("brief")
        if isinstance(brief, dict) and brief:
            brief_text = " · ".join(
                f"{key}: {_cell(value)}" for key, value in brief.items()
            )
            lines += [f"브리프 — {brief_text}", ""]
        if isinstance(sequence.get("expected_effect"), str):
            lines += [
                f"기대 효과 — {_cell(sequence['expected_effect'])}",
                "",
            ]
        lines += [
            "| 순서 | 구간 | 내용 | 근거 |",
            "|---|---|---|---|",
        ]
        for cut in sequence["cuts"]:
            start, end = cut["span"]
            note = _cell(cut.get("note") or cut.get("role") or "")
            basis = [
                f"[{_cell(checkpoint_id)}"
                + (
                    f"@{_cell(str(pins[checkpoint_id]).split(':', 1)[-1][:12])}"
                    if checkpoint_id in pins
                    else ""
                )
                + "]("
                + md_target(
                    f"{log_name}#{checkpoint_fragment(checkpoint_id)}"
                )
                + ")"
                for checkpoint_id in (cut.get("checkpoint_ids") or [])
            ] + [
                _cell(signal) for signal in (cut.get("signals") or [])
            ]
            lines.append(
                f"| {cut['order']} "
                f"| {fmt_ts_compact(float(start))}–"
                f"{fmt_ts_compact(float(end))} "
                f"| {note or '-'} | {' · '.join(basis)} |"
            )
        rejected = sequence.get("alternatives_rejected") or []
        if rejected:
            lines += ["", "기각 대안:", ""]
            for alternative in rejected:
                span = alternative.get("span")
                span_text = (
                    f"{fmt_ts_compact(float(span[0]))}–"
                    f"{fmt_ts_compact(float(span[1]))}"
                    if isinstance(span, list) and len(span) == 2
                    else "-"
                )
                lines.append(
                    f"- {span_text} — "
                    f"{_cell(alternative.get('reason') or '')}"
                )
        overrides = sequence.get("human_overrides") or []
        if overrides:
            lines += ["", "사람 수정 이력:", ""]
            lines += [f"- {_cell(item)}" for item in overrides]
    return "\n".join(lines) + "\n"
