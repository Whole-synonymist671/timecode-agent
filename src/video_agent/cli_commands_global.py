"""Command adapters that create or operate across workspaces."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def cmd_runtime(args) -> int:
    from .runtime_config import (
        Feature,
        RuntimeConfig,
        load_runtime_config,
        resolve_asr_backend,
        resolve_clip_encoder,
        runtime_config_path,
        save_runtime_config,
        set_runtime_value,
    )

    if args.runtime_action == "prepare":
        from .runtime_prepare import prepare_runtime_assets

        result = prepare_runtime_assets()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for backend, state in result.items():
                print(f"{backend}: {state}")
        return 0

    if args.runtime_action == "reset":
        config = RuntimeConfig()
        path = save_runtime_config(config)
        print(f"{path} — all-on balanced 기본값으로 복원")
        return 0

    config = load_runtime_config()
    if args.runtime_action == "set":
        try:
            config = set_runtime_value(config, args.key, args.value)
        except ValueError as exc:
            raise ValueError(str(exc)) from None
        path = save_runtime_config(config)
        print(f"{path} — {args.key}={args.value}")
        return 0

    report = {
        "config": str(runtime_config_path()),
        "profile": config.profile.value,
        "asr_backend": config.asr_backend.value,
        "resolved_asr_backend": resolve_asr_backend(config).value,
        "clip_encoder": config.clip_encoder.value,
        "resolved_clip_encoder": resolve_clip_encoder(config).value,
        "features": {
            feature.value: config.features.get(feature, True)
            for feature in Feature
        },
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"profile: {report['profile']}")
        print(
            "asr-backend: "
            f"{report['asr_backend']} → {report['resolved_asr_backend']}"
        )
        print(
            "clip-encoder: "
            f"{report['clip_encoder']} → {report['resolved_clip_encoder']}"
        )
        print(
            "features: "
            + " ".join(
                f"{name}={'on' if enabled else 'off'}"
                for name, enabled in report["features"].items()
            )
        )
        print(f"config: {report['config']}")
    return 0


def cmd_audit(args) -> int:
    """Print an aggregate readiness report for manifest-backed workspaces."""
    from .corpus_audit import audit_corpus

    report = audit_corpus(
        args.roots or None,
        workspace_paths=getattr(args, "_workspace_paths", None),
        projection_root=getattr(args, "_projection_root", None),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    print(
        f"workspaces={report['workspace_count']} "
        f"terminal={report['terminal_count']} "
        f"supported={report['supported_count']}"
    )
    print(
        f"issues={report['issue_counts']} "
        f"readiness={report['readiness_counts']} "
        f"legacy={report['readiness_legacy_counts']}"
    )
    declared = report.get("verification_levels_declared") or {}
    grounded = report.get("verification_levels_grounded") or {}
    if declared or grounded:
        # 두 축을 함께 인쇄한다. 선언 축만 보이던 동안 실측 코퍼스의 무근거
        # 46/165가 요약에서 사라졌다 — 계산해 두고 말하지 않으면 사람은
        # "전부 근거 있음"으로 읽는다(2026-07-27).
        ko = {"cross_modal": "교차모달", "visual_only": "시각단독",
              "transcript_only": "전사단독", "unsupported": "무근거"}

        def _fmt(levels: dict[str, int], *, show_zero_unsupported: bool) -> str:
            items = dict(levels)
            if show_zero_unsupported:
                items.setdefault("unsupported", 0)
            return " · ".join(
                f"{ko.get(code, code)} {count}"
                for code, count in items.items())

        print("검증 수준(선언): " + _fmt(declared, show_zero_unsupported=False))
        # 실지지 축은 무근거 0도 인쇄한다 — 경계가 비었다는 사실 자체가 신호다.
        print("검증 수준(실지지): " + _fmt(grounded, show_zero_unsupported=True))
    wiki = report.get("wiki")
    if wiki:
        over = " 초과!" if wiki["corpus_index_over_limit"] else ""
        print(
            f"wiki: 엔티티 {wiki['entities']}"
            f"(정착 {wiki['durable_entities']}"
            f"/후보 {wiki['candidate_entities']}) "
            f"관계 {wiki['relations']} 대사 {wiki['quotes']} "
            f"서술 {wiki['notes_filled']} "
            f"통합후보 {wiki['alias_groups']} "
            f"인덱스 {wiki['corpus_index_bytes'] / 1024:.1f}KB{over}"
        )
    candidate_total = report.get("relation_candidate_total")
    if candidate_total:
        # 엣지를 자동으로 만들지 않는다 — 술어는 사람이 판정해 체크포인트에
        # 적고, 그 원장을 거쳐 위키 관계로 나온다.
        print(
            f"관계 후보: {candidate_total}건 "
            "(엔티티 2+ 인데 relations 미기재 — --json의 "
            "relation_candidates에서 대상 확인)"
        )
    access = report.get("access") or {}
    if access.get("total"):
        by_command = " · ".join(
            f"{name} {count}" for name, count in access["by_command"].items())
        empty = access.get("empty_result_reads") or 0
        # 결과 0건 조회는 코퍼스가 답하지 못한 질문 — 커버리지 공백의 신호다.
        tail = f" · 무응답 {empty}" if empty else ""
        print(f"조회: 총 {access['total']}회 ({by_command})"
              f"{tail} · 최근 {access['last_ts']}")
    else:
        print("조회: 기록 없음 — 이 코퍼스가 읽힌 적이 있는지 알 수 없다")
    integrity = report.get("wiki_integrity")
    if integrity:
        broken = integrity["broken_links"]
        orphans = integrity["orphan_entities"]
        # 재배정은 링크가 살아 있는 채로 대상만 바뀌는 회귀라, 깨진 링크
        # 검사로는 절대 잡히지 않는다.
        reassigned = integrity.get("reassigned_entities") or []
        print(f"무결성: 링크 깨짐 {len(broken)} · 고아 페이지 {len(orphans)}"
              f" · 슬러그 재배정 {len(reassigned)}")
        for item in (broken + orphans)[:8]:
            print(f"  ✗ {item}")
        for item in reassigned[:8]:
            print(f"  ↻ {item}")
    improvements = report.get("improvements")
    if improvements and any(improvements.values()):
        print("개선 후보:")
        label_ko = {
            "notes_missing_durable": "서술 없는 정착 엔티티",
            "relations_missing_recurring": "관계 없는 다회 등장",
            "narrative_missing_converged": "서사 미작성 완결 영상",
        }
        for key, items in improvements.items():
            if items:
                head = " · ".join(items[:6])
                more = f" 외 {len(items)-6}" if len(items) > 6 else ""
                print(f"  {label_ko[key]} {len(items)}: {head}{more}")
    for workspace in report["workspaces"]:
        if (
            workspace["issue_counts"]
            or workspace["readiness"] != workspace["readiness_legacy"]
        ):
            print(
                f"{workspace['path']}: readiness={workspace['readiness']} "
                f"legacy={workspace['readiness_legacy']} "
                f"terminal={workspace['terminal_count']} "
                f"supported={workspace['supported_count']} "
                f"issues={workspace['issue_counts']}"
            )
    return 0


def cmd_ingest(args) -> int:
    from .ingest import ingest_session

    if not args.video.strip():
        # 빈 문자열은 Path("")가 cwd로 해석돼 "video not found: <cwd>" 라는
        # 사용자가 준 값과 무관해 보이는 메시지가 나온다.
        raise ValueError("video 인자가 비어 있습니다 — 파일 경로 또는 "
                         "http(s) URL을 지정하십시오")
    with ingest_session(
        args.video,
        out=Path(args.out) if args.out else None,
        model=args.model,
        asr_backend=getattr(args, "asr_backend", "auto"),
        lang=args.lang,
        force_whisper=args.force_whisper,
        max_height=args.max_height,
        hotwords=args.hotwords,
        cookies_from_browser=args.cookies_from_browser,
        signals=args.signals,
    ) as ws:
        if args.signals:
            from .brief import build_brief

            print(build_brief(ws))
            print(f"workspace: {ws.root}")
            return 0
        m = ws.manifest
        print(f"workspace: {ws.root}")
        print(f"transcript: {ws.transcript_path} "
              f"({m['segments']} segments, source={m['transcript_source']})")
    return 0


def cmd_bridge(args) -> int:
    # 에러는 main()의 공통 래핑으로 — 여기서 stdout에 찍으면 파이프라인에서
    # 실패 메시지가 성공 출력에 섞인다(다른 모든 명령은 stderr).
    from .bridge import publish_bridge

    corpus = Path(getattr(args, "_projection_root", args.corpus))
    report = publish_bridge(
        corpus,
        Path(args.vault),
        workspace_paths=getattr(args, "_workspace_paths", None),
    )
    print(
        f"{report['dest']} — 스텁 {report['published']}개 발행"
        f" (stale 제거 {report['removed']}, 외부 파일 보존 {report['foreign']})"
    )
    return 0


def cmd_glossary(args) -> int:
    from .glossary import GLOSSARY_PATH, load_hotwords, update_glossary

    frozen = getattr(args, "_glossary_paths", None)
    if frozen is not None:
        targets = list(frozen)
    else:
        targets = [Path(w) for w in args.workspaces]
        if args.all:
            root = Path.cwd() / "va-out"
            # rglob: 워크스페이스는 va-out/쇼츠/<ws>처럼 분류 폴더 밑에도 산다.
            # 1단계 glob은 그런 워크스페이스를 통째로 놓쳤다
            # (실측 2026-07-25: 7개 중 3개만 수집).
            targets += sorted(p.parent for p in root.rglob("corrections.jsonl"))
    if targets:
        path, added, total = update_glossary(targets)
        print(f"{path} — +{added} 용어 (총 {total})")
    else:
        hot = load_hotwords()
        if hot:
            print(hot)
            print(f"({len(hot.split())}개 — {GLOSSARY_PATH})",
                  file=sys.stderr)
        else:
            print("glossary 비어 있음 — corrections.jsonl이 있는 워크스페이스로 "
                  "`va glossary <ws>...` 실행", file=sys.stderr)
    return 0


def _resolve_corpus(args) -> Path | None:
    from .workspace_discovery import MultipleProjectionRootsError, corpus_root

    explicit = getattr(args, "_projection_root", None)
    if explicit:
        return Path(explicit)
    try:
        return corpus_root(args.roots or None)
    except (MultipleProjectionRootsError, OSError, ValueError):
        return None


def _record_corpus_read(args, command: str, **detail) -> None:
    """조회를 관측한다 — 실패해도 조회 자체는 막지 않는다."""
    from .access_log import record_access

    corpus = _resolve_corpus(args)
    if corpus is None:
        return
    record_access(corpus, command, **detail)


def cmd_search(args) -> int:
    from .search import search_workspaces

    hits = search_workspaces(
        args.query,
        roots=args.roots or None,
        top=args.top,
        workspace_paths=getattr(args, "_workspace_paths", None),
        projection_root=getattr(args, "_projection_root", None),
    )
    _record_corpus_read(args, "search", query=args.query, hits=len(hits))
    if args.json:
        print(json.dumps(hits, ensure_ascii=False))
    else:
        for h in hits:
            source = h["source"]
            if status := h.get("status"):
                source = f"{source}|{status}"
            print(f"{h['ws']}  {h['start']:.1f}s–{h['end']:.1f}s  "
                  f"[{source}]  {h['text'][:70]}")
        if not hits:
            print("no matches", file=sys.stderr)
    return 0


def cmd_index(args) -> int:
    from .index import build_index

    dest, n = build_index(
        args.roots or None,
        graph_reset=getattr(args, "graph_reset", False),
        workspace_paths=getattr(args, "_workspace_paths", None),
        projection_root=getattr(args, "_projection_root", None),
    )
    _record_corpus_read(args, "index", hits=n)
    print(f"{dest} — {n}개 워크스페이스")
    return 0


def cmd_view(args) -> int:
    from .view import build_view

    dest, n = build_view(
        args.roots or None,
        workspace_paths=getattr(args, "_workspace_paths", None),
        projection_root=getattr(args, "_projection_root", None),
    )
    _record_corpus_read(args, "view", hits=n)
    print(f"{dest} — {n}개 워크스페이스")
    return 0


def cmd_wiki(args) -> int:
    from .wiki import build_wiki

    dest, c = build_wiki(
        args.roots or None,
        include_hypotheses=args.include_hypotheses,
        workspace_paths=getattr(args, "_workspace_paths", None),
        projection_root=getattr(args, "_projection_root", None),
    )
    _record_corpus_read(args, "wiki", hits=c["entities"])
    print(f"{dest} — 엔티티 {c['entities']} · 태그 {c['tags']} · "
          f"관계 {c['relations']} · 대사 {c['quotes']}")
    return 0


def cmd_gc(args) -> int:
    from .gc import run_gc

    text, code = run_gc(
        paths=args.paths,
        purge=args.purge,
        keep_days=args.keep_days,
        yes=args.yes,
    )
    print(text)
    return code
