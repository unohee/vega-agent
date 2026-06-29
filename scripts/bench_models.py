#!/usr/bin/env python3
# Created: 2026-06-23
# Purpose: Smoke harness 러너 — bench_lib 위 thin CLI (INT-1876).
"""단일-shot 모델 벤치 — OpenRouter + Judge + verify."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import importlib.util
_SPEC = importlib.util.spec_from_file_location("bench_lib", REPO / "scripts" / "bench_lib.py")
bl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bl)

JUDGE_MODEL = bl.JUDGE_MODEL


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="@tier1",
                    help="콤마 구분 모델 id 또는 @curated")
    ap.add_argument("--categories", default="", help="office,swe,multilingual")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--task-ids", default="", help="콤마 구분 task id 필터")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    cats = [c.strip() for c in args.categories.split(",") if c.strip()] or None
    tasks = bl.load_tasks(categories=cats, harness="smoke")
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

    print(f"[bench-smoke] 모델 {len(models)} × 태스크 {len(tasks)} = {len(models)*len(tasks)} 런")
    for m in models:
        print(f"  - model: {m}")
    for t in tasks:
        print(f"    · {t['category']}/{t['id']} (rubric {len(t['rubric'])}항목)")

    if args.dry_run:
        print("[bench-smoke] --dry-run: API 호출 없이 종료.")
        return 0

    key = bl._resolve_key()
    if not key:
        print("[bench-smoke] ERROR: OPENROUTER_API 키 없음.")
        return 1

    results = []
    for m in models:
        for t in tasks:
            sd = REPO / "build_output" / "bench_verify" / m.replace("/", "_")
            r = bl.run_smoke_task(m, t, key, sandbox_dir=sd)
            tag = "ERR" if r.get("error") else ("PASS" if r.get("pass") else "fail")
            print(f"  [{tag}] {m} · {t['category']}/{t['id']} ratio={r.get('ratio','-')} "
                  f"exec={r.get('exec_pass','-')} lat={r.get('latency_sec','-')}s")
            results.append(r)

    summary, by_cat, by_task, _by_src = bl.summarize_results(results)
    print("\n[bench-smoke] 모델별 요약:")
    for m, s in summary.items():
        print(f"  {m}: pass {s['pass']}/{s['n']} · mean_ratio {s.get('mean_ratio',0)}")

    if args.out:
        artifact = bl.build_artifact(results, harness="smoke")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[bench-smoke] 저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
