#!/usr/bin/env python3
# Created: 2026-06-23
# Purpose: 업무별 모델 벤치마크 하니스 (EPIC INT-1876 / INT-1889·1890·1891).
#          동일 태스크를 후보 모델별로 OpenRouter에서 실행 → 출력·토큰·지연 계측 →
#          Claude Sonnet 평가자로 rubric 채점 → PASS/FAIL 리포트.
# Dependencies: OPENROUTER_API 키(env 또는 keychain), data/bench_tasks.json
# Usage:
#   python scripts/bench_models.py --dry-run                      # 무과금: 태스크·계획만 검증
#   python scripts/bench_models.py --models deepseek/deepseek-v4-flash --categories office --limit 1
#   python scripts/bench_models.py --models a,b,c --out build_output/bench.json
"""모델 벤치마크 러너.

설계:
- 후보 모델은 INT-1888 큐레이션 카탈로그(≤$1/Mtok·caching)에서 고른다.
- 평가 모델은 Claude Sonnet (운영자 결정, EPIC). rubric 각 항목 0~2점.
- 실제 LLM 호출은 OpenRouter /chat/completions(비스트리밍) 직접 — 게이트웨이 우회로 모델 고정.
- --dry-run 은 API 호출 없이 태스크 로딩·계획만 검증(예산 0, CI/스모크용).
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TASKS_PATH = REPO / "data" / "bench_tasks.json"
JUDGE_MODEL = "anthropic/claude-sonnet-4-6"
_OR_URL = "https://openrouter.ai/api/v1/chat/completions"


def load_tasks(path: Path = TASKS_PATH, categories: list[str] | None = None) -> list[dict]:
    """bench_tasks.json 을 평탄화해 [{id, category, prompt, rubric}] 로 반환."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cats = data.get("categories", {})
    out: list[dict] = []
    for cat, tasks in cats.items():
        if categories and cat not in categories:
            continue
        for t in tasks:
            out.append({**t, "category": cat})
    return out


def _resolve_key() -> str:
    key = os.getenv("OPENROUTER_API", "")
    if not key:
        try:
            from pipeline import keychain
            key = keychain.get_secret("OPENROUTER_API") or ""
        except Exception:
            pass
    return key


def _or_chat(model: str, messages: list[dict], key: str, max_tokens: int = 1500) -> dict:
    """OpenRouter 비스트리밍 호출 → {text, tokens_in, tokens_out, latency_sec}."""
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(
        _OR_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/unohee/VEGA", "X-Title": "VEGA-bench"},
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
    latency = round(time.monotonic() - t0, 2)
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    usage = data.get("usage") or {}
    return {"text": text, "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0), "latency_sec": latency}


def _parse_judge(raw: str) -> list[dict]:
    """평가자 출력에서 JSON 배열([{criterion, score, note}])을 관대하게 추출."""
    s = raw.strip()
    a, b = s.find("["), s.rfind("]")
    if a < 0 or b < 0:
        return []
    try:
        arr = json.loads(s[a:b + 1])
        return [x for x in arr if isinstance(x, dict) and "score" in x]
    except Exception:
        return []


def aggregate(scores: list[dict]) -> dict:
    """rubric 점수 배열 → {total, max, ratio, pass}. 각 항목 만점 2점, ratio≥0.7 면 pass."""
    total = sum(min(2, max(0, int(x.get("score", 0)))) for x in scores)
    mx = 2 * len(scores)
    ratio = round(total / mx, 3) if mx else 0.0
    return {"total": total, "max": mx, "ratio": ratio, "pass": bool(mx) and ratio >= 0.7}


def judge(task: dict, output: str, key: str, judge_model: str = JUDGE_MODEL) -> dict:
    """Claude Sonnet 으로 rubric 채점. 각 항목 0~2점 JSON 반환 요구."""
    rubric = "\n".join(f"{i+1}. {c}" for i, c in enumerate(task["rubric"]))
    sys = ("너는 엄격한 평가자다. 아래 과제 출력이 각 rubric 항목을 얼마나 충족하는지 "
           "0(미충족)·1(부분)·2(충족)으로 채점하라. 반드시 JSON 배열만 출력: "
           '[{"criterion":"...","score":0,"note":"..."}]')
    usr = f"[과제]\n{task['prompt']}\n\n[출력]\n{output}\n\n[rubric]\n{rubric}"
    res = _or_chat(judge_model, [{"role": "system", "content": sys},
                                 {"role": "user", "content": usr}], key, max_tokens=900)
    scores = _parse_judge(res["text"])
    return {**aggregate(scores), "scores": scores}


def run_task(model: str, task: dict, key: str) -> dict:
    """한 모델로 한 태스크 실행 + 채점 → 결과 dict."""
    try:
        run = _or_chat(model, [{"role": "user", "content": task["prompt"]}], key)
    except Exception as e:
        return {"model": model, "task": task["id"], "category": task["category"], "error": str(e)[:200]}
    verdict = judge(task, run["text"], key)
    return {"model": model, "task": task["id"], "category": task["category"],
            "tokens_in": run["tokens_in"], "tokens_out": run["tokens_out"],
            "latency_sec": run["latency_sec"], "ratio": verdict["ratio"],
            "pass": verdict["pass"], "scores": verdict["scores"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="deepseek/deepseek-v4-flash",
                    help="콤마 구분 모델 id (큐레이션 카탈로그에서 선택 권장)")
    ap.add_argument("--categories", default="", help="콤마 구분: office,swe,multilingual (기본 전체)")
    ap.add_argument("--limit", type=int, default=0, help="카테고리당 최대 태스크 수 (0=전체)")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 계획만 검증(예산 0)")
    ap.add_argument("--out", default="", help="결과 JSON 저장 경로")
    args = ap.parse_args()

    cats = [c.strip() for c in args.categories.split(",") if c.strip()] or None
    tasks = load_tasks(categories=cats)
    if args.limit:
        by_cat: dict = {}
        kept = []
        for t in tasks:
            by_cat.setdefault(t["category"], 0)
            if by_cat[t["category"]] < args.limit:
                kept.append(t); by_cat[t["category"]] += 1
        tasks = kept
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    print(f"[bench] 모델 {len(models)} × 태스크 {len(tasks)} = {len(models)*len(tasks)} 런 "
          f"(평가자 {JUDGE_MODEL})")
    for m in models:
        print(f"  - model: {m}")
    for t in tasks:
        print(f"    · {t['category']}/{t['id']} (rubric {len(t['rubric'])}항목)")

    if args.dry_run:
        print("[bench] --dry-run: API 호출 없이 종료(예산 0).")
        return 0

    key = _resolve_key()
    if not key:
        print("[bench] ERROR: OPENROUTER_API 키 없음 (env/keychain)."); return 1

    results = []
    for m in models:
        for t in tasks:
            r = run_task(m, t, key)
            tag = "ERR" if r.get("error") else ("PASS" if r.get("pass") else "fail")
            print(f"  [{tag}] {m} · {t['category']}/{t['id']} "
                  f"ratio={r.get('ratio','-')} out_tok={r.get('tokens_out','-')} "
                  f"lat={r.get('latency_sec','-')}s")
            results.append(r)

    # 모델별 요약
    summary: dict = {}
    for r in results:
        s = summary.setdefault(r["model"], {"pass": 0, "n": 0, "tok_out": 0, "lat": 0.0})
        s["n"] += 1
        s["pass"] += 1 if r.get("pass") else 0
        s["tok_out"] += r.get("tokens_out", 0)
        s["lat"] += r.get("latency_sec", 0.0)
    print("\n[bench] 모델별 요약:")
    for m, s in summary.items():
        print(f"  {m}: pass {s['pass']}/{s['n']} · 평균 out_tok {s['tok_out']//max(1,s['n'])} "
              f"· 평균 {round(s['lat']/max(1,s['n']),1)}s")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"results": results, "summary": summary},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[bench] 저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
