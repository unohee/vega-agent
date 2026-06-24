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
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TASKS_PATH = REPO / "data" / "bench_tasks.json"
JUDGE_MODEL = os.getenv("BENCH_JUDGE_MODEL", "anthropic/claude-sonnet-4-6")
_OR_URL = "https://openrouter.ai/api/v1/chat/completions"
RULES_PATH = REPO / "data" / "agents" / "RULES.md"


def task_prompt(task: dict) -> str:
    """태스크 프롬프트 — 보도자료는 RULES.md 컨텍스트 주입 (INT-1889)."""
    prompt = task["prompt"]
    if task.get("id") == "press_release_single" and RULES_PATH.is_file():
        rules = RULES_PATH.read_text(encoding="utf-8")[:4000]
        prompt = f"[보도자료 규칙 — RULES.md]\n{rules}\n\n[과제]\n{prompt}"
    return prompt


def detect_cjk_hallucination(text: str) -> bool:
    """비한국어 문자(가나·한자-only) 혼입 검출 (INT-1891)."""
    if re.search(r"[\u3040-\u30ff]", text):
        return True
    hangul = len(re.findall(r"[가-힣]", text))
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    return han > 2 and hangul == 0


def _extract_python_code(text: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    if "def " in text:
        lines = []
        for line in text.splitlines():
            if line.strip().startswith("def ") or lines:
                lines.append(line)
        return "\n".join(lines).strip()
    return ""


def verify_swe(task: dict, output: str) -> dict:
    """SWE 태스크 — 추출 코드 실행 검증 (INT-1890)."""
    tid = task.get("id", "")
    code = _extract_python_code(output)
    if not code:
        return {"exec_pass": False, "exec_error": "no_code_extracted"}
    ns: dict = {}
    try:
        exec(code, ns)  # noqa: S102 — bench harness isolated exec
        if tid == "py_bugfix":
            avg = ns.get("avg")
            if avg is None:
                return {"exec_pass": False, "exec_error": "avg_not_defined"}
            if avg([]) != 0.0 or avg([2, 4]) != 3.0:
                return {"exec_pass": False, "exec_error": "avg_wrong_result"}
            return {"exec_pass": True}
        if tid == "py_implement":
            top_word = ns.get("top_word")
            if top_word is None:
                return {"exec_pass": False, "exec_error": "top_word_not_defined"}
            w, c = top_word("Hello, hello! World.")
            if w.lower() != "hello" or c != 2:
                return {"exec_pass": False, "exec_error": "top_word_wrong_result"}
            return {"exec_pass": True}
    except Exception as e:
        return {"exec_pass": False, "exec_error": str(e)[:200]}
    return {"exec_pass": None}


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
    key = os.getenv("OPENROUTER_API", "") or os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        try:
            from pipeline import keychain
            key = keychain.get_secret("OPENROUTER_API") or ""
        except Exception:
            pass
    return key


def _format_http_error(e: urllib.error.HTTPError) -> str:
    """OpenRouter HTTPError 본문에서 actionable 메시지 추출."""
    try:
        raw = e.read().decode()
        data = json.loads(raw)
        err = data.get("error") or data
        if isinstance(err, dict):
            msg = err.get("message") or str(err.get("code", raw))
        else:
            msg = str(err)
        return f"HTTP {e.code}: {msg}"
    except Exception:
        return f"HTTP {e.code}: {e.reason}"


def _preflight(key: str, probe_model: str) -> str | None:
    """첫 태스크 전 OpenRouter 연결·예산 확인. 실패 시 에러 문자열."""
    try:
        _or_chat(probe_model, [{"role": "user", "content": "ping"}], key, max_tokens=1)
    except RuntimeError as e:
        return str(e)
    return None


def _or_chat(model: str, messages: list[dict], key: str, max_tokens: int = 1500) -> dict:
    """OpenRouter 비스트리밍 호출 → {text, tokens_in, tokens_out, latency_sec}."""
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(
        _OR_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/unohee/VEGA", "X-Title": "VEGA-bench"},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(_format_http_error(e)) from e
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
    agg = aggregate(scores)
    if not scores:
        agg["error"] = "judge_parse_failed"
        agg["judge_raw"] = res["text"][:300]
    return {**agg, "scores": scores}


def run_task(model: str, task: dict, key: str) -> dict:
    """한 모델로 한 태스크 실행 + 채점 → 결과 dict."""
    prompt = task_prompt(task)
    try:
        run = _or_chat(model, [{"role": "user", "content": prompt}], key)
    except Exception as e:
        return {"model": model, "task": task["id"], "category": task["category"], "error": str(e)[:200]}
    out: dict = {
        "model": model,
        "task": task["id"],
        "category": task["category"],
        "tokens_in": run["tokens_in"],
        "tokens_out": run["tokens_out"],
        "latency_sec": run["latency_sec"],
        "tool_call_count": 0,
    }

    try:
        verdict = judge(task, run["text"], key)
    except Exception as e:
        out["judge_error"] = str(e)[:220]
        out.update({"ratio": 0.0, "pass": False, "scores": []})
    else:
        out.update({
            "ratio": verdict["ratio"],
            "pass": verdict["pass"],
            "scores": verdict["scores"],
        })
        if verdict.get("error"):
            out["judge_error"] = verdict["error"]
            if verdict.get("judge_raw"):
                out["judge_raw"] = verdict["judge_raw"]

    if task["category"] == "swe":
        swe = verify_swe(task, run["text"])
        out.update(swe)
        if swe.get("exec_pass") is False:
            out["pass"] = False
            out["judge_error"] = out.get("judge_error") or "swe_verify_failed"

    if task.get("id") == "lang_hallucination_guard":
        out["hallucination_chars_detected"] = detect_cjk_hallucination(run["text"])
        if out["hallucination_chars_detected"]:
            out["pass"] = False
            out["judge_error"] = out.get("judge_error") or "hallucination_detected"

    return out


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

    if err := _preflight(key, models[0]):
        print(f"[bench] ERROR: OpenRouter preflight failed ({models[0]}): {err}")
        low = err.lower()
        if "budget limit" in low or "monthly limit" in low:
            print("[bench] hint: OpenRouter org 월 예산 한도 초과 — 대시보드에서 한도 상향 또는 리셋 대기.")
        return 1

    results = []
    for m in models:
        for t in tasks:
            r = run_task(m, t, key)
            tag = "judge_error" if r.get("judge_error") else ("ERR" if r.get("error") else ("PASS" if r.get("pass") else "fail"))
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
        artifact = {
            "schema_version": 1,
            "judge_model": JUDGE_MODEL,
            "results": results,
            "summary": summary,
        }
        Path(args.out).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[bench] 저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
