"""Cross-workspace lexical search over accumulated understanding artifacts.

The kilobyte-scale text artifacts (transcript segments, checkpoints, OCR
pseudo-transcript) of every workspace under a va-out root are indexed into an
in-memory SQLite FTS5 table at query time — no persistent index, so nothing
can go stale. Korean text is NFC-normalized on both sides (macOS paths arrive
NFD) and query terms are prefix-matched so particle-suffixed forms still hit
("치킨" matches "치킨을"). BM25 ranking; plain substring scan as fallback when
FTS finds nothing (e.g. single-syllable queries).
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from .checkpoints import load_checkpoints
from .transcript_segments import load_transcript_segments
from .workspace import Workspace
from .workspace_discovery import corpus_root as corpus_root
from .workspace_discovery import find_workspaces


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _to_text(value) -> str:
    """Flatten str | dict | list checkpoint fields to searchable text.

    Real workspaces store speakers/entities either as plain strings or as
    dicts like {"label": "멤버A", "basis": "명패"} — index the string values
    of both shapes.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_to_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_to_text(v) for v in value)
    return ""


# ASR 세그먼트는 짧다(실측 코퍼스 평균 20자). 구절이 경계로 갈리면 "A B"
# 질의가 어느 세그먼트에도 온전히 들어있지 않아 통째로 놓친다. 인접 세그먼트를
# 묶은 윈도우를 보조 색인으로 함께 넣고, 히트는 다시 실제 세그먼트로 좁힌다.
_WINDOW_SEGMENTS = 3
# 무발화로 멀리 떨어진 세그먼트를 한 윈도우로 묶으면 없는 근접성을 만들어낸다.
_WINDOW_MAX_GAP_S = 5.0

# 검색 계열 — 문서 수가 아니라 계열 내 순위로 겨루게 한다. 전사는 체크포인트
# 보다 수십 배 많아 단일 랭킹에서는 이해가 원시 전사에 묻힌다.
_SOURCE_FAMILY = {
    "checkpoint": "understanding",
    "transcript": "transcript",
    "transcript-window": "transcript",
    "ocr": "screen",
}
_RRF_K = 60  # reciprocal rank fusion 표준 상수


def _family(source: str) -> str:
    return _SOURCE_FAMILY.get(source.split(":")[0], "other")


def _transcript_windows(seg_docs: list[dict]) -> list[dict]:
    """인접 전사 세그먼트 윈도우 — 경계로 갈린 구절 회상용 보조 문서."""
    windows: list[dict] = []
    total = len(seg_docs)
    for i in range(total):
        members = [seg_docs[i]]
        for j in range(i + 1, min(i + _WINDOW_SEGMENTS, total)):
            if seg_docs[j]["start"] - members[-1]["end"] > _WINDOW_MAX_GAP_S:
                break
            members.append(seg_docs[j])
        if len(members) < 2:
            continue
        windows.append({
            "ws": members[0]["ws"],
            "source": "transcript-window",
            "start": members[0]["start"],
            "end": members[-1]["end"],
            "text": " ".join(m["text"] for m in members),
            "members": members,
        })
    return windows


def collect_docs(ws: Workspace, *, windows: bool = True) -> list[dict]:
    """One doc per searchable unit: transcript seg / checkpoint / OCR span.

    본문은 여기서 한 번 NFC로 맞춰 둔다(macOS 경로·파일은 NFD로 들어온다) —
    이후 색인·점수 계산은 정규화를 다시 하지 않는다.

    windows=False면 전사 윈도우를 만들지 않는다. 윈도우는 공백으로 이어붙인
    보조 문서라 토큰도 부분 문자열도 멤버 경계를 넘지 못한다 — 단일어 질의에서
    윈도우가 맞으면 그 멤버 세그먼트도 반드시 맞으므로 회상 이득이 없고
    색인 비용만 는다.
    """
    docs: list[dict] = []
    name = ws.root.name
    segs = load_transcript_segments(ws.transcript_path)
    seg_docs = [{"ws": name, "source": "transcript",
                 "start": s["start"], "end": s["end"],
                 "text": _nfc(s["text"].strip())}
                for s in segs if s.get("text", "").strip()]
    docs.extend(seg_docs)
    if windows:
        docs.extend(_transcript_windows(seg_docs))
    for c in load_checkpoints(ws):
        parts = [_to_text(c.get("hypothesis")),
                 _to_text(c.get("situation")),
                 _to_text(c.get("note")),
                 _to_text(c.get("speakers")),
                 _to_text(c.get("entities"))]
        text = _nfc(" ".join(p for p in parts if p).strip())
        if text:
            span = c.get("span")
            if (
                not isinstance(span, (list, tuple))
                or len(span) != 2
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    for value in span
                )
            ):
                continue
            start, end = span
            docs.append({"ws": name, "source": f"checkpoint:{c['id']}",
                         "status": c["status"],
                         "start": start, "end": end,
                         "text": text})
    ocr_path = ws.root / "ocr_transcript.json"
    if ocr_path.is_file():
        try:
            for o in json.loads(ocr_path.read_text(encoding="utf-8")):
                if o.get("text", "").strip():
                    docs.append({"ws": name, "source": "ocr",
                                 "start": o["start"], "end": o["end"],
                                 "text": _nfc(o["text"].strip())})
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return docs


def _fts_query(query: str) -> str:
    """Each term as a prefix phrase: 치킨 순간 -> "치킨"* "순간"*."""
    terms = [t for t in _nfc(query).split() if t]
    return " ".join('"{}"*'.format(t.replace('"', '""')) for t in terms)


def _resolve_windows(hits: list[dict], terms: list[str]) -> list[dict]:
    """윈도우 히트를 말이 실제로 나온 세그먼트로 좁히고 중복 구간을 접는다.

    윈도우는 회상을 넓히려고 둔 보조 색인이다. 결과까지 윈도우 구간으로
    돌려주면 인용이 뭉뚱그려지므로, 질의어를 가장 많이 담은 멤버 세그먼트의
    타임스탬프로 되돌린다. 컷은 여기서 하지 않는다 — 융합이 top을 정한다.
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for hit in hits:              # 점수 내림차순 — 먼저 온 쪽이 최고 점수다
        if hit.get("members"):
            best = max(hit["members"],
                       key=lambda m: sum(m["text"].count(t) for t in terms))
            hit = {**hit, "source": "transcript", "start": best["start"],
                   "end": best["end"], "text": best["text"]}
            del hit["members"]
        key = (hit["ws"], hit["source"], hit["start"], hit["end"])
        if key not in seen:
            seen.add(key)
            out.append(hit)
    return out


def _fts_rank(docs: list[dict], query: str, limit: int) -> list[dict]:
    """BM25-ranked FTS5 search over one family of documents."""
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE VIRTUAL TABLE d USING fts5(text, tokenize='unicode61')")
    con.executemany("INSERT INTO d(rowid, text) VALUES (?, ?)",
                    [(i, doc["text"]) for i, doc in enumerate(docs)])
    hits: list[dict] = []
    fq = _fts_query(query)
    if fq:
        try:
            rows = con.execute(
                "SELECT rowid, rank FROM d WHERE d MATCH ? "
                "ORDER BY rank LIMIT ?", (fq, limit)).fetchall()
            hits = [{**docs[r], "score": round(-rank, 2)} for r, rank in rows]
        except sqlite3.OperationalError:
            hits = []
    con.close()
    return hits


def _substring_rank(
    docs: list[dict], terms: list[str], limit: int
) -> list[dict]:
    """Fallback for sub-token matches the tokenizer splits away ("디어")."""
    hits: list[dict] = []
    for doc in docs:
        text = doc["text"]
        count = sum(text.count(t) for t in terms)
        if count and all(t in text for t in terms):
            hits.append({**doc, "score": float(count)})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


def _rrf_fuse(ranked_lists: list[list[dict]], top: int) -> list[dict]:
    """Reciprocal rank fusion — 계열의 문서 수가 아니라 계열 내 순위로 겨룬다."""
    fused: dict[tuple, dict] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            key = (hit["ws"], hit["source"], hit["start"], hit["end"])
            contribution = 1.0 / (_RRF_K + rank)
            if key in fused:
                fused[key]["score"] += contribution
            else:
                fused[key] = {**hit, "score": contribution}
    out = sorted(fused.values(), key=lambda h: -h["score"])
    for hit in out:
        hit["score"] = round(hit["score"], 4)
    return out[:top]


def search_docs(docs: list[dict], query: str, top: int = 10) -> list[dict]:
    """계열별 BM25 랭킹을 RRF로 융합한다(부분 문자열 스캔 폴백).

    전사는 체크포인트보다 문서가 수십 배 많아, 단일 랭킹에서는 같은 말이
    전사에 여러 번 나오기만 해도 상위를 덮고 판정된 이해가 밀려난다. 전사·
    이해·화면을 따로 랭킹해 융합하면 각 계열의 최상위가 자리를 얻는다.

    반환 score는 BM25 값이 아니라 RRF 점수다 — 순위 신호이며 절대 크기에
    의미가 없다.
    """
    terms = [t for t in _nfc(query).split() if t]
    if not terms:
        return []
    families: dict[str, list[dict]] = {}
    for doc in docs:
        families.setdefault(_family(doc["source"]), []).append(doc)
    # 세그먼트와 그것을 품은 윈도우가 함께 맞으므로, 좁히고 접은 뒤 top을
    # 채우려면 넉넉히 뽑아야 한다.
    pool = top * 4
    # 폴백은 계열마다 판정한다. 전역으로 보면 한 계열이 FTS로 맞는 순간
    # 나머지 계열의 사이 토큰 적중이 통째로 사라진다 — "디어"가 이해에는
    # 단독 토큰으로 있고 전사에는 "드디어" 안에만 있을 때 전사를 놓쳤다.
    # 전사 계열 전체 스캔은 실측 0.7ms(4,008 doc)로 회상과 바꿀 값이 아니다.
    ranked: list[list[dict]] = []
    for family_docs in families.values():
        hits = _fts_rank(family_docs, query, pool)
        if not hits:
            hits = _substring_rank(family_docs, terms, pool)
        if hits:
            ranked.append(_resolve_windows(hits, terms))
    return _rrf_fuse(ranked, top)


def search_workspaces(
    query: str,
    roots: list[str] | None = None,
    top: int = 10,
    *,
    workspace_paths: Sequence[Path] | None = None,
) -> list[dict]:
    docs: list[dict] = []
    # 단일어 질의는 윈도우에서 얻을 회상이 없다(멤버 경계를 넘는 매칭이
    # 불가능하다) — 만들지도 색인하지도 않는다.
    windows = len(_nfc(query).split()) >= 2
    paths = (
        list(workspace_paths)
        if workspace_paths is not None
        else find_workspaces(roots)
    )
    for root in paths:
        try:
            docs.extend(collect_docs(Workspace.load(root), windows=windows))
        except FileNotFoundError:
            continue
    if not docs:
        raise ValueError(
            "no searchable workspaces found (looked in: "
            + ", ".join(roots or ["./va-out"]) + ")")
    return search_docs(docs, query, top=top)
