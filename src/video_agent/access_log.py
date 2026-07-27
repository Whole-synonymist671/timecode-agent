"""Append-only record of corpus reads.

원장 셋(`checkpoints`·`sequences`·`image-provenance`)이 무엇을 알게 됐는지
적는다면, 이 파일은 그 지식이 실제로 읽혔는지를 적는다. 조회는 증거가
아니므로 revision에 결박하지 않는다 — 결박하면 소스 교체 때 조회 이력이
끊기는데, 조회 이력의 가치는 정확히 그 연속성에 있다.

쿼리 원문은 남기지 않는다. 조회 이력도 노출 표면이고, 소비량을 관측하는 데
원문은 필요 없다.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TypedDict

ACCESS_LOG_FILENAME: Final = ".tca-access-log.jsonl"


class AccessSummary(TypedDict):
    total: int
    by_command: dict[str, int]
    empty_result_reads: int
    last_ts: str | None


def access_log_path(corpus: Path) -> Path:
    return Path(corpus) / ACCESS_LOG_FILENAME


def record_access(
    corpus: Path,
    command: str,
    *,
    query: str | None = None,
    hits: int | None = None,
) -> None:
    """Append one read record, never failing the read it observes."""
    entry: dict[str, object] = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": command,
    }
    if query is not None:
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
        entry["query_sha256"] = f"sha256:{digest}"
        entry["query_chars"] = len(query)
    if hits is not None:
        entry["hits"] = hits
    try:
        path = access_log_path(corpus)
        with path.open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # 계측은 조회에 종속된다. 반대가 되면 관측이 기능을 망가뜨린다.
        return


def read_access_log(corpus: Path) -> list[dict]:
    path = access_log_path(corpus)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    entries: list[dict] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            # 잘린 마지막 줄은 버리고 나머지 이력을 살린다.
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


def summarize_access_log(corpus: Path) -> AccessSummary:
    entries = read_access_log(corpus)
    by_command: Counter[str] = Counter()
    empty_result_reads = 0
    last_ts: str | None = None
    for entry in entries:
        command = entry.get("command")
        if isinstance(command, str):
            by_command[command] += 1
        hits = entry.get("hits")
        if isinstance(hits, int) and not isinstance(hits, bool) and hits == 0:
            empty_result_reads += 1
        ts = entry.get("ts")
        if isinstance(ts, str) and (last_ts is None or ts > last_ts):
            last_ts = ts
    return {
        "total": len(entries),
        "by_command": dict(sorted(by_command.items())),
        "empty_result_reads": empty_result_reads,
        "last_ts": last_ts,
    }
