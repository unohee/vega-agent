#!/usr/bin/env python3
# Created: 2026-06-24
# Purpose: smoke + agent 벤치 artifact 병합 → build_output/bench.json (v2).
"""Operator: bench_smoke.json + bench_agent.json → routing용 bench.json."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def merge(smoke_path: Path, agent_path: Path | None, external_path: Path | None = None) -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location("bench_lib", REPO / "scripts" / "bench_lib.py")
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)

    results = []
    judge = "anthropic/claude-sonnet-4-6"
    if smoke_path.is_file():
        smoke = _load(smoke_path)
        results.extend(smoke.get("results") or [])
        judge = smoke.get("judge_model", judge)
    if agent_path and agent_path.is_file():
        agent = _load(agent_path)
        results.extend(agent.get("results") or [])
        judge = agent.get("judge_model", judge)
    if external_path and external_path.is_file():
        ext = _load(external_path)
        results.extend(ext.get("results") or [])
        judge = ext.get("judge_model", judge)

    artifact = bl.build_artifact(results, harness="merged")
    artifact["schema_version"] = 2
    artifact["sources"] = {
        "smoke": str(smoke_path) if smoke_path.is_file() else None,
        "agent": str(agent_path) if agent_path and agent_path.is_file() else None,
        "external": str(external_path) if external_path and external_path.is_file() else None,
    }
    artifact["judge_model"] = judge
    return artifact


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", default="build_output/bench_smoke.json")
    ap.add_argument("--agent", default="build_output/bench_agent.json")
    ap.add_argument("--external", default="", help="bench_external_merged.json path")
    ap.add_argument("--out", default="build_output/bench.json")
    args = ap.parse_args()
    smoke = REPO / args.smoke
    agent = REPO / args.agent if args.agent else None
    external = REPO / args.external if args.external else None
    out = REPO / args.out
    artifact = merge(smoke, agent, external)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[merge-bench] {len(artifact['results'])} results → {out}")
    print(f"  summary_by_category: {artifact.get('summary_by_category', {})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
