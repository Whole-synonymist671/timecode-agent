"""관계 후보 — 엣지를 만들지 않고, 사람이 확정할 자리만 가리킨다.

코퍼스 위키의 관계는 체크포인트의 relations 필드에 s·p·o가 명시될 때만
생긴다. 그래서 엔티티는 쌓이는데 관계는 거의 자라지 않는다(실측 코퍼스에서
엔티티 83 대 관계 11).

두 엔티티가 한 구간에 함께 나왔다는 사실은 근접일 뿐 관계가 아니다. 술어를
추측해 자동으로 엣지를 만들면 근거 없는 연결이 그래프를 오염시키므로, 여기서는
"관계가 있을 법한데 비어 있는 자리"만 짚는다. 판정은 사람이 하고, 결과는
체크포인트에 적혀 원장을 거쳐 위키로 나온다.

코퍼스 순회는 여기 없다 — 이미 원장을 한 번 읽은 감사(corpus_audit)가
소유하고, 이 모듈은 그 결과에 적용할 순수 술어만 갖는다.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypedDict

from .corpus_projection import relation_triples, speaker_labels


class RelationCandidate(TypedDict):
    """A checkpoint that names two or more entities but records no relation."""

    ws: str
    checkpoint: str | None
    entities: list[str]
    span: list[float] | None
    hypothesis: str


def _entity_labels(checkpoint: dict) -> list[str]:
    """화자와 등장 개체를 한 집합으로 — 관계의 항은 둘을 가리지 않는다."""
    labels = speaker_labels(checkpoint.get("entities"))
    labels += speaker_labels(checkpoint.get("speakers"))
    return sorted({label.strip() for label in labels if label.strip()})


def candidates_from_checkpoints(
    ws_name: str, checkpoints: Iterable[dict]
) -> list[RelationCandidate]:
    """이미 읽어 둔 원장에서 후보를 고른다 — 감사가 원장을 다시 읽지 않게."""
    candidates: list[RelationCandidate] = []
    for checkpoint in checkpoints:
        if relation_triples(checkpoint):
            continue
        entities = _entity_labels(checkpoint)
        if len(entities) < 2:
            continue
        span = checkpoint.get("span")
        candidates.append({
            "ws": ws_name,
            "checkpoint": checkpoint.get("id"),
            "entities": entities,
            "span": list(span) if isinstance(span, (list, tuple)) else None,
            "hypothesis": str(checkpoint.get("hypothesis") or ""),
        })
    return candidates
