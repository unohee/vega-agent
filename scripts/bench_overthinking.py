#!/usr/bin/env python3
# Created: 2026-06-24
# Purpose: INT-1893 L3 overthinking scenario bench (dry-run routing + optional live).
# Usage:
#   python scripts/bench_overthinking.py --dry-run
#   python scripts/bench_overthinking.py --live --reps 1 --out build_output/overthinking_bench.json
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SCENARIOS_PATH = REPO / "data" / "overthinking_scenarios.json"


def load_scenarios() -> list[dict]:
    data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    return data.get("scenarios") or []


def dry_run_row(sc: dict) -> dict:
    from pipeline.tier_router import resolve_load_routing
    from pipeline.tools import get_schemas_for_mode, TOOL_SCHEMAS

    routing = resolve_load_routing(sc["messages"])
    tools = get_schemas_for_mode(TOOL_SCHEMAS, load=routing["load"])
    exp = sc.get("expect") or {}
    row = {
        "id": sc["id"],
        "load": routing["load"],
        "max_rounds": routing["max_rounds"],
        "max_tool_rounds": routing["max_tool_rounds"],
        "budget_max_tokens": routing["budget"].get("max_tokens"),
        "tool_schema_count": len(tools),
        "expect_ok": routing["load"] == exp.get("load"),
    }
    return row


async def live_run(sc: dict, tier: str = "local") -> dict:
    from pipeline import streaming

    stats: dict = {}

    async def on_token(_t: str) -> None:
        pass

    def fake_sse(req, token_q, tool_q, kind="chat_completions", stats_out=None, loop=None, reasoning_q=None):
        if sc["id"].startswith("ikea"):
            streaming._queue_put(token_q, "1. LAMPAN 9900원\n2. SOLHETTA 12900원", loop)
        else:
            streaming._queue_put(token_q, "완료.", loop)
        streaming._queue_put(token_q, None, loop)
        streaming._queue_put(tool_q, None, loop)

    t0 = time.monotonic()
    with __import__("unittest.mock").mock.patch("pipeline.streaming._build_request") as br, \
         __import__("unittest.mock").mock.patch("pipeline.streaming._stream_sse", side_effect=fake_sse), \
         __import__("unittest.mock").mock.patch("pipeline.streaming.build_dynamic_preamble", return_value=""), \
         __import__("unittest.mock").mock.patch("pipeline.model_catalog.resolve_turn_model", return_value=None):
        br.return_value = (__import__("unittest.mock").mock.MagicMock(), "chat_completions")
        await streaming.stream_gpt(
            sc["messages"],
            streaming.build_system(load=resolve_load(sc["messages"])),
            on_token=on_token,
            stats=stats,
            tier=tier,
        )
    stats["elapsed_sec"] = round(time.monotonic() - t0, 2)
    stats["id"] = sc["id"]
    return stats


def resolve_load(messages: list[dict]) -> str:
    from pipeline.tier_router import resolve_load_routing
    return resolve_load_routing(messages)["load"]


def summarize(rows: list[dict], key: str) -> dict:
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    if not vals:
        return {}
    return {
        "median": round(statistics.median(vals), 2),
        "p95": round(sorted(vals)[min(len(vals) - 1, int(len(vals) * 0.95))], 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="routing/tools only (no API)")
    ap.add_argument("--live", action="store_true", help="mock streaming integration runs")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--phase", default="baseline", help="artifact label")
    ap.add_argument("--out", default="", help="JSON output path")
    args = ap.parse_args()

    scenarios = load_scenarios()
    results: list[dict] = []

    if args.dry_run or not args.live:
        print("[overthinking-bench] dry-run routing")
        for sc in scenarios:
            row = dry_run_row(sc)
            ok = "OK" if row["expect_ok"] else "FAIL"
            print(f"  [{ok}] {row['id']}: load={row['load']} tools={row['tool_schema_count']} "
                  f"max_rounds={row['max_rounds']} tool_cap={row['max_tool_rounds']}")
            results.append(row)

    if args.live:
        print("[overthinking-bench] live mock streaming")
        live_rows = []
        for _ in range(max(1, args.reps)):
            for sc in scenarios:
                live_rows.append(asyncio.run(live_run(sc)))
        for r in live_rows:
            print(f"  {r.get('id')}: actual_rounds={r.get('actual_rounds')} "
                  f"tool_rounds={r.get('tool_rounds')} load={r.get('load')}")
        results.extend(live_rows)

    artifact = {
        "schema_version": 1,
        "phase": args.phase,
        "results": results,
        "summary": {
            "light_actual_rounds": summarize([r for r in results if r.get("load") == "light"], "actual_rounds"),
            "light_tool_rounds": summarize([r for r in results if r.get("load") == "light"], "tool_rounds"),
        },
    }
    out = Path(args.out) if args.out else REPO / "build_output" / f"overthinking_{args.phase}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[overthinking-bench] saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
