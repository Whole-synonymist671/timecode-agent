"""Lexical qualifier-preservation audit between checkpoint ledger and narrative.

체크포인트가 항상 한정어("후보"·"추정" 등)와 함께만 언급한 용어가
에이전트 저작 서사(narrative block)에서 한정어 없이 확정 표현으로
등장하면 강등 신호로 계상한다. 어휘 대조라 의미 함의(entailment)는
판정하지 못한다 — 자문 감사이며 빌드를 차단하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TypedDict

from .checkpoint_schema import CheckpointObject
from .corpus_projection import narrative_block
from .workspace import Workspace

QUALIFIER_MARKERS: tuple[str, ...] = (
    "후보",
    "추정",
    "미확인",
    "의심",
    "가능성",
    "불확실",
    "미검증",
)
_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")
# 원장 쪽은 쉼표를 절 경계로 보지 않는다 — "A, B 후보" 열거의 한정어가
# 열거 전체를 감싸도록 관대하게 잡아 감시 어휘 누락을 줄인다.
_CLAUSE_SPLIT_RE = re.compile(r"[.!?。\n;]|—")
# 서사 쪽은 쉼표도 절 경계다 — 한 절의 한정어가 같은 줄의 다른 확정
# 주장("요격은 확정, 추진은 후보")까지 면제하지 않게 엄격하게 본다.
_NARRATIVE_CLAUSE_RE = re.compile(r"[.!?。\n;,，]|—")
_TEXT_KEYS = ("hypothesis", "situation", "note")
# 마크다운 강조(**요격**)·따옴표가 어절 선두에 붙어도 경계 매칭이 뚫리지
# 않게 벗겨낸다.
_EOJEOL_LEADING_PUNCT = "'\"“”‘’«»『』「」·*_~`([{<"
# 괄호 내용은 별도 절로 추출하고 바깥 텍스트는 이어 붙인다 —
# "(코인노래방 추정)"의 마커가 밖으로 새지 않으면서(오탐 "가는" 1차
# 원인), "요격(화면 불명확) 후보"의 꼬리 한정어는 요격과 같은 절에
# 남는다(경계 분할 방식의 재현율 손실 방지).
_PAREN_RE = re.compile(r"\(([^()]*)\)")
# 기승전결 템플릿 라벨 접두("- 전(전환):"·"전환:")만 문맥 제거 —
# 본문 속 "장면 전환" 같은 실제 주장 어휘는 감시를 유지한다.
_ARC_LABEL_RE = re.compile(
    r"^\s*[-*•]?\s*(?:[기승전결]\s*\(\s*)?(?:도입|전개|전환|마무리)\s*\)?\s*[:：]"
)


class QualifierDropIssue(TypedDict):
    """One narrative line that states a qualified-only term as fact."""

    token: str
    line: str


def _clause_has_token(clause: str, token: str) -> bool:
    """어절 시작 매칭 — "찾아가는" 내부의 "가는" 같은 부분문자열 오탐 차단.

    조사·어미가 뒤에 붙은 형태("요격은")는 잡고, 다른 단어 속에 묻힌
    형태는 잡지 않는다(실측 오탐 "가는"의 2차 원인 봉합).
    """
    return any(
        eojeol.lstrip(_EOJEOL_LEADING_PUNCT).startswith(token)
        for eojeol in clause.split()
    )


def _flatten_parentheticals(raw: str) -> list[str]:
    """괄호 내용을 별도 조각으로 추출하고 바깥은 공백으로 이어 붙인다."""
    inners: list[str] = []
    outer = raw
    while True:
        outer, replaced = _PAREN_RE.subn(
            lambda match: inners.append(match.group(1)) or " ", outer
        )
        if not replaced:
            break
    return [outer, *inners]


def _checkpoint_clauses(
    checkpoints: Sequence[CheckpointObject],
) -> list[str]:
    clauses: list[str] = []
    for checkpoint in checkpoints:
        for key in _TEXT_KEYS:
            raw = checkpoint.get(key)
            if isinstance(raw, str) and raw.strip():
                clauses.extend(
                    clause
                    for part in _flatten_parentheticals(raw)
                    for clause in _CLAUSE_SPLIT_RE.split(part)
                    if clause.strip()
                )
    return clauses


def qualified_only_tokens(
    checkpoints: Sequence[CheckpointObject],
) -> frozenset[str]:
    """Tokens every checkpoint mention of which carries a qualifier marker."""
    qualified: set[str] = set()
    unqualified: set[str] = set()
    for clause in _checkpoint_clauses(checkpoints):
        tokens = set(_TOKEN_RE.findall(clause))
        if any(marker in clause for marker in QUALIFIER_MARKERS):
            qualified.update(tokens)
        else:
            unqualified.update(tokens)
    return frozenset(qualified - unqualified - set(QUALIFIER_MARKERS))


def audit_narrative_qualifiers(
    ws: Workspace, checkpoints: Sequence[CheckpointObject]
) -> list[QualifierDropIssue]:
    """Flag narrative lines that drop the ledger's qualifiers from a term."""
    tokens = qualified_only_tokens(checkpoints)
    if not tokens:
        return []
    issues: list[QualifierDropIssue] = []
    for line in narrative_block(ws).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "(", "<!--")):
            continue
        # 템플릿 라벨 접두만 제거하고, 괄호는 절 경계가 아니라 같은 절의
        # 부가 정보로 본다 — "요격(후보)"의 마커가 자기 절을 면제한다.
        body = _ARC_LABEL_RE.sub("", stripped, count=1)
        body = body.replace("(", " ").replace(")", " ")
        flagged: set[str] = set()
        for clause in _NARRATIVE_CLAUSE_RE.split(body):
            if any(marker in clause for marker in QUALIFIER_MARKERS):
                continue
            flagged.update(
                token for token in tokens
                if _clause_has_token(clause, token)
            )
        issues.extend(
            {"token": token, "line": stripped[:120]}
            for token in sorted(flagged)
        )
    return issues
