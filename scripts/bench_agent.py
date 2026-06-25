#!/usr/bin/env python3
# Created: 2026-06-24
# Purpose: Agent harness — stream_gpt E2E 벤치 (INT-1876 확장).
"""프로덕션 stream_gpt + 도구 경로로 office 태스크 측정."""
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
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_SPEC = importlib.util.spec_from_file_location("bench_lib", REPO / "scripts" / "bench_lib.py")
bl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bl)


def _bench_openrouter_provider() -> dict:
    from pipeline.llm_gateway import _read_config
    cfg = _read_config()
    prov = dict((cfg.get("providers") or {}).get("openrouter") or {})
    if not prov:
        prov = {
            "name": "openrouter",
            "kind": "chat_completions",
            "auth_type": "bearer",
            "api_key_env": "OPENROUTER_API",
            "base_url": "https://openrouter.ai/api/v1",
        }
    prov["name"] = "openrouter"
    return prov



def _sandbox_for(model: str, task_id: str, run_id: str) -> Path:
    d = REPO / "build_output" / "bench_runs" / run_id / model.replace("/", "_") / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


async def run_agent_task(
    model: str,
    task: dict,
    key: str,
    *,
    sandbox_dir: Path,
    load: str = "standard",
    tier: str = "cloud",
) -> dict:
    from pipeline import streaming

    if task.get("fixture"):
        import shutil
        rel = task["fixture"]
        src = bl.FIXTURES_PATH / rel
        if not src.is_file():
            src = bl.EXTERNAL_ROOT / rel
        if src.is_file():
            dst = sandbox_dir / Path(rel).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    prompt = bl.task_prompt(task, harness="agent")
    messages = [{"role": "user", "content": prompt}]
    system = streaming.build_system(working_dir=str(sandbox_dir), load=load)
    stats: dict = {}
    tokens: list[str] = []

    async def on_token(t: str) -> None:
        tokens.append(t)

    t0 = time.monotonic()
    bench_model = model

    or_prov = _bench_openrouter_provider()
    with patch("pipeline.model_catalog.resolve_turn_model", return_value=bench_model), \
         patch("pipeline.llm_gateway.get_provider_for_tier", return_value=or_prov), \
         patch("pipeline.llm_gateway.get_active_provider", return_value=or_prov):
        try:
            final = await streaming.stream_gpt(
                messages,
                system,
                on_token=on_token,
                working_dir=str(sandbox_dir),
                stats=stats,
                tier=tier,
                load_override=load,
            )
        except Exception as e:
            return {
                "model": model,
                "task": task["id"],
                "category": task["category"],
                "harness": "agent",
                "error": str(e)[:200],
            }

    latency = round(time.monotonic() - t0, 2)
    output = final or "".join(tokens)
    artifacts = [str(p) for p in sandbox_dir.rglob("*.xlsx")]
    artifacts.extend(str(p) for p in sandbox_dir.rglob("*.pptx"))
    artifacts.extend(str(p) for p in sandbox_dir.rglob("*.py"))

    verify: dict = {}
    if task.get("category") == "swe":
        verify = bl.verify_swe(task, output, sandbox_dir=sandbox_dir)
    elif bl.office_task_needs_verify(task):
        verify = bl.verify_office(task, output, sandbox_dir=sandbox_dir, artifacts=artifacts)

    verify_first = bl.task_is_verify_first(task)
    skip_judge = verify_first and (
        task.get("category") == "swe" or bl.office_task_needs_verify(task)
    )
    if skip_judge:
        verdict = {"ratio": 0.0, "pass": False, "scores": []}
    else:
        verdict = bl.judge(task, output, key)

    if verify_first and verify.get("exec_pass") is True:
        verdict["ratio"] = 1.0
        verdict["pass"] = True
    subjective = not (verify_first and task.get("id") != "excel_read_fix")
    tool_verify = bl.verify_tool_use(task, stats)
    passed = bl.merge_agent_pass(
        task,
        judge_pass=verdict["pass"],
        verify=verify or None,
        tool_verify=tool_verify,
        subjective=subjective,
    )

    tools_called = tool_verify.get("tools_called") or stats.get("tools_called") or []
    tool_rounds = stats.get("tool_rounds", 0)
    out = {
        "model": model,
        "task": task["id"],
        "category": task["category"],
        "harness": "agent",
        "tokens_in": stats.get("tokens_in", 0) or stats.get("input_tokens", 0),
        "tokens_out": stats.get("tokens_out", 0) or stats.get("output_tokens", 0),
        "latency_sec": latency,
        "tool_call_count": len(tools_called),
        "tool_rounds": tool_rounds,
        "tools_called": tools_called,
        "required_tools_met": tool_verify.get("required_tools_met"),
        "tool_score": tool_verify.get("tool_score"),
        "tool_pass": tool_verify.get("tool_pass"),
        "tool_verify": tool_verify,
        "actual_rounds": stats.get("actual_rounds"),
        "ratio": verdict["ratio"],
        "pass": passed,
        "scores": verdict.get("scores", []),
        "verify": verify or None,
        "artifacts": artifacts,
        "sandbox_dir": str(sandbox_dir),
        "selected_model": stats.get("selected_model", model),
        "load": stats.get("load", load),
    }
    if task.get("source"):
        out["source"] = task["source"]
    if task.get("suite"):
        out["suite"] = task["suite"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="@tier1")
    ap.add_argument("--categories", default="office")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--limit-models", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--task-ids", default="", help="콤마 구분 task id 필터")
    ap.add_argument("--load", default="standard")
    args = ap.parse_args()

    cats = [c.strip() for c in args.categories.split(",") if c.strip()] or None
    tasks = bl.load_tasks(categories=cats, harness="agent")
    if args.task_ids:
        allow = {x.strip() for x in args.task_ids.split(',') if x.strip()}
        tasks = [t for t in tasks if t['id'] in allow]
    if args.limit:
        by_cat: dict = {}
        kept = []
        for t in tasks:
            by_cat.setdefault(t["category"], 0)
            if by_cat[t["category"]] < args.limit:
                kept.append(t)
                by_cat[t["category"]] += 1
        tasks = kept
    models = bl.parse_models_arg(args.models)
    if args.limit_models:
        models = models[: args.limit_models]

    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    print(f"[bench-agent] run_id={run_id} models={len(models)} tasks={len(tasks)} load={args.load}")

    for m in models:
        print(f"  - model: {m}")
    for t in tasks:
        print(f"    · {t['category']}/{t['id']}")

    if args.dry_run:
        for m in models[:1] or ["dry"]:
            for t in tasks:
                sd = _sandbox_for(m, t["id"], run_id)
                print(f"    sandbox: {sd}")
        print("[bench-agent] --dry-run: 종료.")
        return 0

    key = bl._resolve_key()
    if not key:
        print("[bench-agent] ERROR: OPENROUTER_API 키 없음.")
        return 1

    os.environ.setdefault("OPENROUTER_API", key)
    results = []
    for m in models:
        for t in tasks:
            sd = _sandbox_for(m, t["id"], run_id)
            r = asyncio.run(run_agent_task(m, t, key, sandbox_dir=sd, load=args.load))
            tag = "ERR" if r.get("error") else ("PASS" if r.get("pass") else "fail")
            print(f"  [{tag}] {m} · {t['id']} tools={r.get('tool_rounds',0)} "
                  f"called={r.get('tools_called',[])} tool_pass={r.get('tool_pass')} "
                  f"exec={r.get('verify',{}).get('exec_pass') if r.get('verify') else '-'} "
                  f"lat={r.get('latency_sec')}s")
            results.append(r)

    if args.out:
        artifact = bl.build_artifact(results, harness="agent")
        artifact["run_id"] = run_id
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[bench-agent] 저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
