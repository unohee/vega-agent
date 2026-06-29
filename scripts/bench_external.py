#!/usr/bin/env python3
# Created: 2026-06-25
# Purpose: Run external benchmark routing subsets via VEGA smoke/agent harness.
"""External bench runner — manifest → smoke/agent → build_output/bench_external_*.json."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_SPEC = importlib.util.spec_from_file_location("bench_lib", REPO / "scripts" / "bench_lib.py")
bl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bl)

_SPEC_A = importlib.util.spec_from_file_location("bench_agent", REPO / "scripts" / "bench_agent.py")
ba = importlib.util.module_from_spec(_SPEC_A)
_SPEC_A.loader.exec_module(ba)


def _resolve_suites(raw: str) -> list[str]:
    manifest = json.loads(bl.MANIFEST_PATH.read_text(encoding="utf-8"))
    if raw.strip() == "routing" or raw.strip() == "all":
        return list(manifest.get("routing_suites") or [])
    return [s.strip() for s in raw.split(",") if s.strip()]


def _filter_tasks(tasks: list[dict], harness: str) -> list[dict]:
    if harness == "auto":
        return tasks
    if harness == "smoke":
        return [t for t in tasks if t.get("harness", "smoke") in ("smoke", "both")]
    return [t for t in tasks if t.get("harness") in ("agent", "both")]


async def _run_agent_batch(tasks: list[dict], models: list[str], load: str, key: str) -> list[dict]:
    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    results: list[dict] = []
    for m in models:
        for t in tasks:
            sd = ba._sandbox_for(m, t["id"], run_id)
            r = await ba.run_agent_task(m, t, key, sandbox_dir=sd, load=load)
            results.append(r)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites", default="routing", help="routing|all|comma suite names")
    ap.add_argument("--models", default="@tier1")
    ap.add_argument("--harness", default="auto", choices=("auto", "smoke", "agent"))
    ap.add_argument("--load", default="standard")
    ap.add_argument("--limit-models", type=int, default=0)
    ap.add_argument("--limit-tasks", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="build_output/bench_external_merged.json")
    ap.add_argument("--wrapper", default="none", choices=("none", "slidesgen", "presentbench"))
    ap.add_argument("--judge", default="", help="openrouter|claude-cli (or env VEGA_BENCH_JUDGE)")
    args = ap.parse_args()

    if args.judge:
        os.environ["VEGA_BENCH_JUDGE"] = args.judge

    suites = _resolve_suites(args.suites)
    smoke_tasks: list[dict] = []
    agent_tasks: list[dict] = []
    for suite in suites:
        st = bl.load_tasks_from_manifest(suites=[suite], harness="smoke")
        at = bl.load_tasks_from_manifest(suites=[suite], harness="agent")
        smoke_tasks.extend(st)
        agent_tasks.extend(at)

    if args.harness == "smoke":
        agent_tasks = []
    elif args.harness == "agent":
        smoke_tasks = []

    if args.limit_tasks:
        smoke_tasks = smoke_tasks[: args.limit_tasks]
        agent_tasks = agent_tasks[: args.limit_tasks]

    models = bl.parse_models_arg(args.models)
    if args.limit_models:
        models = models[: args.limit_models]

    total = len(smoke_tasks) + len(agent_tasks)
    print(f"[bench-external] suites={suites} smoke={len(smoke_tasks)} agent={len(agent_tasks)} "
          f"models={len(models)} wrapper={args.wrapper}")

    if args.dry_run:
        for t in smoke_tasks[:3] + agent_tasks[:3]:
            print(f"  · {t.get('suite')}/{t['id']} harness={t.get('harness')}")
        print(f"[bench-external] dry-run total task slots={total * len(models)}")
        return 0

    key = bl._resolve_key()
    if not key:
        print("[bench-external] ERROR: OPENROUTER_API missing")
        return 1
    os.environ.setdefault("OPENROUTER_API", key)

    results: list[dict] = []
    for t in smoke_tasks:
        for m in models:
            sd = REPO / "build_output" / "bench_verify" / m.replace("/", "_") / t["id"]
            r = bl.run_smoke_task(m, t, key, sandbox_dir=sd)
            tag = "PASS" if r.get("pass") else "fail"
            print(f"  [{tag}] smoke {m} · {t['id']} ratio={r.get('ratio')}")
            results.append(r)

    if agent_tasks:
        agent_results = asyncio.run(_run_agent_batch(agent_tasks, models, args.load, key))
        for r in agent_results:
            tag = "ERR" if r.get("error") else ("PASS" if r.get("pass") else "fail")
            print(f"  [{tag}] agent {r.get('model')} · {r.get('task')} tools={r.get('tool_rounds', 0)}")
        results.extend(agent_results)

    artifact = bl.build_artifact(results, harness="external")
    artifact["external_suites"] = suites
    artifact["wrapper"] = args.wrapper
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bench-external] saved {len(results)} runs → {out}")
    print(f"  summary_by_source: {json.dumps(artifact.get('summary_by_source', {}), ensure_ascii=False)[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
