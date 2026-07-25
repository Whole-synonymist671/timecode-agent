#!/usr/bin/env python3
"""Reproducible regression bench over the 3 protocol videos.

Benchmark-as-regression-gate: the original eval protocols were manual
documents; this runner turns their DETERMINISTIC half into a repeatable
gate. Quality scoring (the 20 questions) stays a manual protocol — this
guards the pipeline invariants underneath it:

  ingest(+signals) wall time, transcript presence/segment counts, scene and
  highlight counts, the brief's mode recommendation, and the readiness gate.

Two fixture sets:
  personal — local user files (this machine only; golden.json)
  public   — CC-BY Blender open-movie fixtures anyone can fetch
             (fixtures_public.json -> --fetch -> golden-public.json).
             The public pair is what a published snapshot ships (ADR-0001).

Any missing video is SKIPPED, so the gate degrades gracefully on other
machines. stats.csv is written per run.

Usage:
    .venv/bin/python benchmarks/run_bench.py                 # all available
    .venv/bin/python benchmarks/run_bench.py --set public --fetch
    .venv/bin/python benchmarks/run_bench.py --capture       # (re)write golden
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from video_agent.brief import build_brief  # noqa: E402
from video_agent.highlights import compute_highlights  # noqa: E402
from video_agent.ingest import ingest  # noqa: E402
from video_agent.scenes import compute_scenes  # noqa: E402
from video_agent.status import workspace_status  # noqa: E402

HERE = Path(__file__).resolve().parent
FIXTURES_DIR = HERE / "fixtures"
PUBLIC_SPEC = HERE / "fixtures_public.json"
# 개인 소재(파일 경로·제목)는 추적 금지(ADR-0001) — gitignore되는 로컬
# 스펙에만 둔다. 파일이 없으면 personal 세트는 자연 skip.
PERSONAL_SPEC = HERE / "fixtures-personal.local.json"

GOLDEN_FILES = {"personal": HERE / "golden.json",
                "public": HERE / "golden-public.json"}
# wall-time ceiling: generous 3x of measured (M4 Pro, small model) so the
# gate catches order-of-magnitude regressions, not scheduler noise.
# personal 항목의 상한은 로컬 스펙의 time_ceiling_s가 채운다.
TIME_CEILING_S = {"ed": 360, "sintel-trailer": 60, "bbb-trailer": 60}


def personal_videos() -> dict[str, Path]:
    if not PERSONAL_SPEC.is_file():
        return {}
    spec = json.loads(PERSONAL_SPEC.read_text(encoding="utf-8"))
    for entry in spec:
        TIME_CEILING_S.setdefault(
            entry["name"], int(entry.get("time_ceiling_s", 120)))
    return {entry["name"]: Path(entry["path"]) for entry in spec}


def public_videos() -> dict[str, Path]:
    spec = json.loads(PUBLIC_SPEC.read_text(encoding="utf-8"))
    return {e["name"]: FIXTURES_DIR / e["filename"] for e in spec}


def fetch_public() -> None:
    import shutil
    import urllib.request

    FIXTURES_DIR.mkdir(exist_ok=True)
    for e in json.loads(PUBLIC_SPEC.read_text(encoding="utf-8")):
        dest = FIXTURES_DIR / e["filename"]
        if dest.is_file():
            print(f"{e['name']}: already fetched")
            continue
        print(f"{e['name']}: downloading {e['url']}")
        # default Python-urllib UA gets 403 from some mirrors (archive.org)
        req = urllib.request.Request(
            e["url"], headers={"User-Agent": "timecode-agent-bench/1.0"})
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(req) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(dest)


def run_one(name: str, video: Path) -> dict:
    with tempfile.TemporaryDirectory() as td:
        t0 = time.time()
        # 명시적 빈 hotwords: 라이브 glossary가 크면(실측 31용어) 세그먼트
        # 밴드가 흔들린다 — 회귀 게이트는 사용자 상태와 무관해야 한다.
        ws = ingest(str(video), out=Path(td) / name, model="small", hotwords="")
        compute_highlights(ws)
        compute_scenes(ws, adaptive=True)
        wall = round(time.time() - t0, 1)
        brief = build_brief(ws)
        mode = next((line.removeprefix("mode: ")
                     for line in brief.splitlines()
                     if line.startswith("mode: ")), "?")
        st = workspace_status(ws)
        return {
            "name": name,
            "wall_s": wall,
            "segments": ws.manifest["segments"],
            "scenes": len(json.loads((ws.root / "scenes.json").read_text())),
            "highlights": len(json.loads(
                (ws.root / "highlights.json").read_text())),
            "mode_head": mode.split(" — ")[0],
            "readiness": st["readiness"]["status"],
        }


def check(row: dict, gold: dict) -> list[str]:
    fails = []
    if row["wall_s"] > TIME_CEILING_S[row["name"]]:
        fails.append(f"wall {row['wall_s']}s > {TIME_CEILING_S[row['name']]}s")
    for key in ("segments", "scenes", "highlights"):
        lo, hi = gold[key + "_range"]
        if not lo <= row[key] <= hi:
            fails.append(f"{key} {row[key]} outside [{lo}, {hi}]")
    if row["mode_head"] != gold["mode_head"]:
        fails.append(f"mode {row['mode_head']!r} != {gold['mode_head']!r}")
    if row["readiness"] != gold["readiness"]:
        fails.append(f"readiness {row['readiness']} != {gold['readiness']}")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", action="store_true",
                    help="write golden from this run instead of checking")
    ap.add_argument("--set", choices=["personal", "public", "all"],
                    default="all", help="fixture set to run (default all)")
    ap.add_argument("--fetch", action="store_true",
                    help="download missing public fixtures first")
    args = ap.parse_args()

    if args.fetch:
        fetch_public()

    sets: dict[str, dict[str, Path]] = {}
    if args.set in ("personal", "all"):
        sets["personal"] = personal_videos()
    if args.set in ("public", "all"):
        sets["public"] = public_videos()

    goldens = {s: (json.loads(GOLDEN_FILES[s].read_text())
                   if GOLDEN_FILES[s].is_file() else {})
               for s in sets}
    rows, failures, skipped = [], [], []
    for set_name, videos in sets.items():
        golden = goldens[set_name]
        for name, video in videos.items():
            if not video.is_file():
                skipped.append(name)
                continue
            row = run_one(name, video)
            rows.append(row)
            if args.capture:
                golden[name] = {
                    # ±20% (min ±2) tolerance bands captured around this run
                    **{k + "_range": [max(0, int(row[k] * 0.8) - 2),
                                      int(row[k] * 1.2) + 2]
                       for k in ("segments", "scenes", "highlights")},
                    "mode_head": row["mode_head"],
                    "readiness": row["readiness"],
                }
            elif name in golden:
                for f in check(row, golden[name]):
                    failures.append(f"{name}: {f}")
            else:
                failures.append(
                    f"{name}: no golden entry (run --capture first)")
            print(f"{name}: {row}")

    if rows:
        with (HERE / "stats.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    if skipped:
        print(f"skipped (fixture missing): {', '.join(skipped)}",
              file=sys.stderr)
    if not rows:
        print("REGRESSION: no videos evaluated", file=sys.stderr)
        return 1
    if args.capture:
        for set_name, golden in goldens.items():
            if golden:
                GOLDEN_FILES[set_name].write_text(
                    json.dumps(golden, ensure_ascii=False, indent=1))
                print(f"golden captured -> {GOLDEN_FILES[set_name]}")
        return 0
    if failures:
        print("REGRESSIONS:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print(f"bench OK ({len(rows)} videos, {len(skipped)} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
