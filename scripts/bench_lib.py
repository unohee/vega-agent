#!/usr/bin/env python3
# Created: 2026-06-24
# Purpose: 공통 벤치 하니스 라이브러리 — smoke/agent 러너 공유 (INT-1876 확장).
"""VEGA 모델 벤치 공통 로직 — 태스크 로딩, Judge, verify_swe/office, artifact v2."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
TASKS_PATH = REPO / "data" / "bench_tasks.json"
FIXTURES_PATH = REPO / "data" / "bench_fixtures"
EXTERNAL_ROOT = REPO / "data" / "bench_external"
MANIFEST_PATH = EXTERNAL_ROOT / "manifest.json"
EXCEL_WINNERS_PATH = REPO / "data" / "bench_excel_winners.json"
TIER1_PATH = REPO / "data" / "bench_tier1_models.json"
JUDGE_MODEL = "anthropic/claude-sonnet-4-6"
JUDGE_BACKENDS = ("openrouter", "claude-cli")
_OR_URL = "https://openrouter.ai/api/v1/chat/completions"
RULES_PATH = REPO / "data" / "agents" / "RULES.md"

_EXCEL_SALES = [120, 95, 140, 110, 130]
_EXCEL_SUM = sum(_EXCEL_SALES)
_EXCEL_AVG = _EXCEL_SUM / len(_EXCEL_SALES)

# verify pass 시 Judge 생략 (verify-first)
VERIFY_FIRST_TASKS = frozenset({
    "excel_calc", "excel_create_e2e", "python_calc_xlsx", "excel_read_fix_save",
    "py_is_palindrome", "py_fizzbuzz", "py_clamp",
    "slide_outline_json", "slide_deck_create", "proposal_json", "ad_copy_json",
})

# agent office 태스크 기본 금지 도구 — host_exec 등 우회 경로 차단
OFFICE_FORBIDDEN_TOOLS = frozenset({"host_exec"})

_PROPOSAL_JSON_KEYS = ("배경", "과업범위", "일정", "예산", "수행조직")
_PROPOSAL_KEYWORDS = ("8000", "8,000", "8000만", "8,000만", "ISO27001", "ISO 27001", "6개월", "9월")


def task_prompt(task: dict, *, harness: str = "smoke") -> str:
    if harness == "agent" and task.get("agent_prompt"):
        prompt = task["agent_prompt"]
    else:
        prompt = task["prompt"]
    if task.get("id") == "press_release_single" and RULES_PATH.is_file():
        rules = RULES_PATH.read_text(encoding="utf-8")[:4000]
        prompt = f"[보도자료 규칙 — RULES.md]\n{rules}\n\n[과제]\n{prompt}"
    if harness == "agent" and task.get("id") == "excel_read_fix":
        fixture = task.get("fixture")
        if fixture:
            fp = FIXTURES_PATH / fixture
            if fp.is_file():
                prompt = f"{prompt}\n\n[입력 파일 경로]\n{fp.expanduser()}"
    return prompt


def detect_cjk_hallucination(text: str) -> bool:
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


def _extract_json_blob(text: str) -> Any | None:
    for pat in (
        r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```",
        r"(\{[\s\S]*\})",
        r"(\[[\s\S]*\])",
    ):
        for m in re.finditer(pat, text):
            raw = m.group(1) if m.lastindex else m.group(0)
            try:
                return json.loads(raw)
            except Exception:
                continue
    return None


def _to_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_alpha(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _verify_slide_outline(blob: Any) -> tuple[bool, str | None, list[str]]:
    checks: list[str] = []
    slides = blob.get("slides") if isinstance(blob, dict) else None
    if not isinstance(slides, list) or len(slides) < 5:
        return False, f"slides_count:{len(slides) if isinstance(slides, list) else 0}", checks
    checks.append("slides_count_ok")
    for i, slide in enumerate(slides[:5]):
        if not isinstance(slide, dict) or not str(slide.get("title", "")).strip():
            return False, f"slide_{i}_missing_title", checks
    checks.append("titles_ok")
    text = json.dumps(blob, ensure_ascii=False).lower()
    if "vega" not in text:
        return False, "missing_vega_keyword", checks
    checks.append("vega_keyword")
    return True, None, checks


def _verify_proposal_json(blob: Any) -> tuple[bool, str | None, list[str]]:
    checks: list[str] = []
    if not isinstance(blob, dict):
        return False, "not_object", checks
    missing = [k for k in _PROPOSAL_JSON_KEYS if k not in blob]
    if missing:
        return False, f"missing_keys:{','.join(missing)}", checks
    checks.append("keys_ok")
    text = json.dumps(blob, ensure_ascii=False)
    hits = sum(1 for kw in _PROPOSAL_KEYWORDS if kw in text)
    if hits < 3:
        return False, f"keyword_hits:{hits}", checks
    checks.append("rfp_keywords")
    for k in _PROPOSAL_JSON_KEYS:
        if len(str(blob.get(k, "")).strip()) < 10:
            return False, f"short_section:{k}", checks
    checks.append("section_length")
    return True, None, checks


def _verify_ad_copy_json(blob: Any) -> tuple[bool, str | None, list[str]]:
    checks: list[str] = []
    if not isinstance(blob, dict):
        return False, "not_object", checks
    for key in ("headline", "body", "cta", "hashtags"):
        if key not in blob:
            return False, f"missing_{key}", checks
    checks.append("keys_ok")
    headline = str(blob["headline"])
    body = str(blob["body"])
    if len(headline) > 30:
        return False, f"headline_len:{len(headline)}", checks
    if len(body) > 150:
        return False, f"body_len:{len(body)}", checks
    checks.extend(["headline_len_ok", "body_len_ok"])
    tags = blob["hashtags"]
    if not isinstance(tags, list) or len(tags) < 3:
        return False, f"hashtags_count:{len(tags) if isinstance(tags, list) else 0}", checks
    checks.append("hashtags_ok")
    text = json.dumps(blob, ensure_ascii=False).lower()
    if "vega" not in text:
        return False, "missing_vega", checks
    checks.append("vega_keyword")
    return True, None, checks


def _verify_pptx_deck(paths: list[str], *, min_slides: int = 5) -> tuple[bool, str | None, list[str], str | None]:
    from pipeline.tools_office import pptx_read

    checks: list[str] = []
    for p in paths:
        rd = pptx_read(p)
        if rd.get("error"):
            continue
        count = rd.get("slide_count", 0)
        if count < min_slides:
            continue
        checks.append("slide_count_ok")
        texts = " ".join(
            t for s in (rd.get("slides") or []) for t in (s.get("texts") or [])
        ).lower()
        if "vega" not in texts:
            continue
        checks.append("vega_keyword")
        return True, None, checks, p
    return False, "pptx_verify_fail", checks, None


def _mom_rates(values: list[float]) -> list[float | None]:
    out: list[float | None] = [None]
    for i in range(1, len(values)):
        prev, cur = values[i - 1], values[i]
        out.append(None if prev == 0 else round((cur - prev) / prev * 100, 2))
    return out


def parse_sheets_json(text: str) -> dict[str, list[list]] | None:
    blob = _extract_json_blob(text)
    if blob is None:
        return None
    if isinstance(blob, dict):
        if blob and all(isinstance(v, list) for v in blob.values()):
            if all(isinstance(row, list) for rows in blob.values() for row in rows):
                return {str(k): rows for k, rows in blob.items()}
        sheet_name = str(blob.get("sheet") or blob.get("sheetName") or blob.get("name") or "매출")
        headers = blob.get("headers") or blob.get("header")
        data = blob.get("data") or blob.get("rows") or blob.get("values")
        if isinstance(data, list) and data:
            rows: list[list] = []
            if headers:
                rows.append(list(headers) if isinstance(headers, list) else [headers])
            for item in data:
                if isinstance(item, list):
                    rows.append(item)
                elif isinstance(item, dict):
                    if headers and isinstance(headers, list):
                        rows.append([item.get(h, "") for h in headers])
                    else:
                        rows.append(list(item.values()))
            if rows:
                return {sheet_name: rows}
        rows_only = blob.get("rows")
        if isinstance(rows_only, list) and rows_only and isinstance(rows_only[0], list):
            return {sheet_name: rows_only}
    return None


def _check_excel_numbers(sheets: dict[str, list[list]], checks: list[str]) -> tuple[bool, str | None]:
    nums: list[float] = []
    for rows in sheets.values():
        for row in rows[1:] if len(rows) > 1 else rows:
            if not isinstance(row, list):
                continue
            for cell in row:
                v = _to_float(cell)
                if v is not None and 50 <= v <= 200 and v == int(v):
                    nums.append(v)
                    break
    target_set = set(_EXCEL_SALES)
    found = [n for n in nums if int(n) in target_set]
    if len(found) >= 5:
        found = found[:5]
    elif len(nums) >= 5:
        found = nums[:5]
    else:
        return False, f"insufficient_sales_values:{nums}"
    if abs(sum(found) - _EXCEL_SUM) > 1:
        return False, f"wrong_sum:{sum(found)}"
    if abs(sum(found) / len(found) - _EXCEL_AVG) > 1:
        return False, f"wrong_avg:{sum(found)/len(found)}"
    pct_vals: list[float] = []
    for rows in sheets.values():
        for row in rows:
            if not isinstance(row, list):
                continue
            for cell in row:
                v = _to_float(cell)
                if v is not None and -100 < v < 100 and v not in found:
                    pct_vals.append(round(v, 1))
    if pct_vals and "mom" in checks:
        exp_non_null = [x for x in _mom_rates([float(x) for x in _EXCEL_SALES])[1:] if x is not None]
        if exp_non_null:
            matched = sum(1 for e in exp_non_null if any(abs(p - e) <= 2.0 for p in pct_vals))
            if matched < len(exp_non_null) // 2:
                return False, f"mom_mismatch:expected~{exp_non_null[:3]} got~{pct_vals[:4]}"
    return True, None


def verify_code_harness(task: dict, output: str) -> dict:
    """HumanEval/MBPP-style exec verify using task test + entry_point."""
    code = _extract_python_code(output)
    if not code:
        return {"exec_pass": False, "exec_error": "no_code_extracted", "checks": []}
    ns: dict = {}
    try:
        exec(code, ns)  # noqa: S102
        test = task.get("test") or ""
        entry_point = task.get("entry_point")
        if entry_point:
            if entry_point not in ns:
                return {"exec_pass": False, "exec_error": f"{entry_point}_not_defined", "checks": []}
            candidate = ns[entry_point]
            if test:
                exec(test, ns)  # noqa: S102
                if "check" in ns:
                    ns["check"](candidate)
                else:
                    return {"exec_pass": False, "exec_error": "check_fn_missing", "checks": []}
        elif test:
            exec(test, ns)  # noqa: S102 — MBPP assert lines
        else:
            return {"exec_pass": False, "exec_error": "no_test_defined", "checks": []}
        return {"exec_pass": True, "checks": ["code_harness_exec"]}
    except Exception as e:
        return {"exec_pass": False, "exec_error": str(e)[:200], "checks": []}


def verify_swebench_micro(task: dict, output: str, *, sandbox_dir: Path | None = None) -> dict:
    snippet = task.get("expected_snippet") or ""
    if snippet and snippet in output:
        return {"exec_pass": True, "checks": ["snippet_in_output"]}
    if sandbox_dir and sandbox_dir.is_dir():
        for p in sandbox_dir.rglob("*"):
            if p.is_file() and p.suffix in (".py", ".txt", ".md"):
                try:
                    if snippet in p.read_text(encoding="utf-8", errors="ignore"):
                        return {"exec_pass": True, "checks": ["snippet_in_artifact"]}
                except Exception:
                    continue
    return {"exec_pass": False, "exec_error": f"snippet_missing:{snippet[:40]}", "checks": []}


def verify_presentbench_checklist(task: dict, output: str, artifacts: list[str] | None = None) -> dict:
    from pipeline.tools_office import pptx_read

    checks: list[str] = []
    checklist = task.get("checklist") or []
    min_slides = int(task.get("min_slides") or 5)
    paths = list(artifacts or [])
    for p in paths:
        if p.endswith(".pptx"):
            rd = pptx_read(p)
            if rd.get("error"):
                continue
            slides = rd.get("slides") or rd.get("slide_count") or 0
            count = slides if isinstance(slides, int) else len(slides)
            if count >= min_slides:
                checks.append("slide_count_ok")
                text_blob = json.dumps(rd, ensure_ascii=False)
                kw_ok = 0
                for item in checklist:
                    kw = item.get("item") if isinstance(item, dict) else str(item)
                    if kw and (kw.lower() in text_blob.lower() or kw in output):
                        kw_ok += 1
                if kw_ok >= max(1, len(checklist) - 1):
                    return {"exec_pass": True, "checks": checks + ["checklist_keywords"]}
    return {"exec_pass": False, "exec_error": "presentbench_pptx_fail", "checks": checks}


def verify_officeeval_spec(task: dict, output: str, artifacts: list[str] | None = None) -> dict:
    from pipeline.tools_office import xlsx_read

    spec = task.get("officeeval_spec") or {}
    paths = [p for p in (artifacts or []) if p.endswith(".xlsx")]
    if not paths:
        return {"exec_pass": False, "exec_error": "no_xlsx", "checks": []}
    rd = xlsx_read(paths[0])
    if rd.get("error"):
        return {"exec_pass": False, "exec_error": rd["error"][:120], "checks": []}
    flat = json.dumps(rd, ensure_ascii=False)
    if "sum" in spec and str(spec["sum"]) not in flat:
        return {"exec_pass": False, "exec_error": f"sum_not_{spec['sum']}", "checks": []}
    if "avg" in spec and str(spec["avg"]) not in flat:
        return {"exec_pass": False, "exec_error": f"avg_not_{spec['avg']}", "checks": []}
    if "headers" in spec:
        for h in spec["headers"]:
            if h not in flat:
                return {"exec_pass": False, "exec_error": f"header_missing_{h}", "checks": []}
    return {"exec_pass": True, "checks": ["officeeval_spec"]}


def verify_adbench_gt(task: dict, output: str) -> dict:
    gt = task.get("adbench_gt") or {}
    needle = gt.get("answer_contains")
    if needle and needle.lower() not in output.lower():
        return {"exec_pass": False, "exec_error": f"missing:{needle}", "checks": []}
    if gt.get("sum_spend") is not None and str(gt["sum_spend"]) not in output.replace(",", ""):
        return {"exec_pass": False, "exec_error": "wrong_spend_sum", "checks": []}
    return {"exec_pass": True, "checks": ["adbench_gt"]}


def verify_bizgeneval_json(task: dict, output: str) -> dict:
    blob = _extract_json_blob(output)
    if not isinstance(blob, dict):
        return {"exec_pass": False, "exec_error": "no_json", "checks": []}
    keys = task.get("bizgeneval_keys") or ["title"]
    missing = [k for k in keys if k not in blob]
    if missing:
        return {"exec_pass": False, "exec_error": f"missing_keys:{missing}", "checks": []}
    title = str(blob.get("title") or blob.get("headline") or "")
    if len(title) > 120:
        return {"exec_pass": False, "exec_error": "title_too_long", "checks": []}
    return {"exec_pass": True, "checks": ["bizgeneval_json"]}


def verify_swe(task: dict, output: str, *, sandbox_dir: Path | None = None) -> dict:
    tid = task.get("id", "")
    src = task.get("source") or task.get("suite") or ""
    if src in ("humaneval", "mbpp") or tid.startswith("ext_humaneval_") or tid.startswith("ext_mbpp_"):
        return verify_code_harness(task, output)
    if src == "swebench_lite" or tid.startswith("ext_swebench_lite_"):
        return verify_swebench_micro(task, output, sandbox_dir=sandbox_dir)
    code = _extract_python_code(output)
    if not code:
        return {"exec_pass": False, "exec_error": "no_code_extracted", "checks": []}
    ns: dict = {}
    try:
        exec(code, ns)  # noqa: S102
        if tid == "py_bugfix":
            avg = ns.get("avg")
            if avg is None:
                return {"exec_pass": False, "exec_error": "avg_not_defined", "checks": []}
            if avg([]) != 0.0 or avg([2, 4]) != 3.0:
                return {"exec_pass": False, "exec_error": "avg_wrong_result", "checks": []}
            return {"exec_pass": True, "checks": ["avg_exec"]}
        if tid == "py_implement":
            top_word = ns.get("top_word")
            if top_word is None:
                return {"exec_pass": False, "exec_error": "top_word_not_defined", "checks": []}
            w, c = top_word("Hello, hello! World.")
            if w.lower() != "hello" or c != 2:
                return {"exec_pass": False, "exec_error": "top_word_wrong_result", "checks": []}
            return {"exec_pass": True, "checks": ["top_word_exec"]}
        if tid == "py_is_palindrome":
            fn = ns.get("is_palindrome")
            if fn is None:
                return {"exec_pass": False, "exec_error": "is_palindrome_not_defined", "checks": []}
            if not fn("A man, a plan, a canal: Panama") or fn("hello"):
                return {"exec_pass": False, "exec_error": "is_palindrome_wrong_result", "checks": []}
            return {"exec_pass": True, "checks": ["is_palindrome_exec"]}
        if tid == "py_fizzbuzz":
            fn = ns.get("fizzbuzz")
            if fn is None:
                return {"exec_pass": False, "exec_error": "fizzbuzz_not_defined", "checks": []}
            expected = []
            for i in range(1, 16):
                if i % 15 == 0:
                    expected.append("FizzBuzz")
                elif i % 3 == 0:
                    expected.append("Fizz")
                elif i % 5 == 0:
                    expected.append("Buzz")
                else:
                    expected.append(str(i))
            if fn(15) != expected:
                return {"exec_pass": False, "exec_error": "fizzbuzz_wrong_result", "checks": []}
            if fn(0) != []:
                return {"exec_pass": False, "exec_error": "fizzbuzz_zero_edge", "checks": []}
            return {"exec_pass": True, "checks": ["fizzbuzz_exec"]}
        if tid == "py_clamp":
            fn = ns.get("clamp")
            if fn is None:
                return {"exec_pass": False, "exec_error": "clamp_not_defined", "checks": []}
            if fn(5, 0, 10) != 5 or fn(-1, 0, 10) != 0 or fn(99, 0, 10) != 10:
                return {"exec_pass": False, "exec_error": "clamp_wrong_result", "checks": []}
            try:
                fn(1, 10, 0)
                return {"exec_pass": False, "exec_error": "clamp_no_value_error", "checks": []}
            except ValueError:
                pass
            return {"exec_pass": True, "checks": ["clamp_exec"]}
    except Exception as e:
        return {"exec_pass": False, "exec_error": str(e)[:200], "checks": []}
    return {"exec_pass": None, "checks": []}


def verify_office(
    task: dict,
    output: str,
    *,
    sandbox_dir: Path | None = None,
    artifacts: list[str] | None = None,
) -> dict:
    from pipeline.tools_office import xlsx_create, xlsx_read

    tid = task.get("id", "")
    checks: list[str] = []

    if tid == "excel_calc":
        sheets = parse_sheets_json(output)
        if not sheets:
            return {"exec_pass": False, "exec_error": "no_sheets_json", "checks": checks}
        ok, err = _check_excel_numbers(sheets, ["sum", "avg", "mom"])
        if not ok:
            return {"exec_pass": False, "exec_error": err, "checks": checks}
        checks.append("numbers_ok")
        tmp = (sandbox_dir or REPO / "build_output" / "bench_verify") / "excel_calc_verify.xlsx"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        cr = xlsx_create(str(tmp), sheets)
        if cr.get("error"):
            return {"exec_pass": False, "exec_error": f"xlsx_create:{cr['error'][:120]}", "checks": checks}
        checks.append("xlsx_create_ok")
        rd = xlsx_read(str(tmp))
        if rd.get("error"):
            return {"exec_pass": False, "exec_error": f"xlsx_read:{rd['error'][:120]}", "checks": checks}
        checks.append("xlsx_read_ok")
        return {"exec_pass": True, "checks": checks, "artifact": str(tmp)}

    if tid == "excel_create_e2e":
        paths = list(artifacts or [])
        if sandbox_dir and sandbox_dir.is_dir():
            paths.extend(str(p) for p in sandbox_dir.rglob("*.xlsx"))
        paths = list(dict.fromkeys(paths))
        if not paths:
            return {"exec_pass": False, "exec_error": "no_xlsx_artifact", "checks": checks}
        for p in paths:
            rd = xlsx_read(p)
            if rd.get("error"):
                continue
            rows = rd.get("rows") or []
            flat = [[c for c in row] for row in rows if isinstance(row, list)]
            ok, err = _check_excel_numbers({"sheet": flat}, ["sum", "avg"])
            if ok:
                checks.extend(["xlsx_exists", "numbers_ok"])
                return {"exec_pass": True, "checks": checks, "artifact": p}
        return {"exec_pass": False, "exec_error": "xlsx_numbers_fail", "checks": checks}

    if tid in ("python_calc_xlsx", "excel_read_fix_save"):
        paths = list(artifacts or [])
        if sandbox_dir and sandbox_dir.is_dir():
            paths.extend(str(p) for p in sandbox_dir.rglob("*.xlsx"))
        paths = list(dict.fromkeys(paths))
        if not paths:
            return {"exec_pass": False, "exec_error": "no_xlsx_artifact", "checks": checks}
        for p in paths:
            rd = xlsx_read(p)
            if rd.get("error"):
                continue
            rows = rd.get("rows") or []
            flat = [[c for c in row] for row in rows if isinstance(row, list)]
            ok, err = _check_excel_numbers({"sheet": flat}, ["sum", "avg"])
            text = json.dumps(flat, ensure_ascii=False)
            if tid == "excel_read_fix_save" and "595" not in text:
                continue
            if ok:
                checks.extend(["xlsx_exists", "numbers_ok"])
                if tid == "excel_read_fix_save":
                    checks.append("fix_sum_ok")
                return {"exec_pass": True, "checks": checks, "artifact": p}
        err_key = "fix_sum_missing" if tid == "excel_read_fix_save" else "xlsx_numbers_fail"
        return {"exec_pass": False, "exec_error": err_key, "checks": checks}

    if tid == "excel_read_fix":
        blob = _extract_json_blob(output)
        if not isinstance(blob, (dict, list)):
            return {"exec_pass": False, "exec_error": "no_fix_json", "checks": checks}
        text = json.dumps(blob, ensure_ascii=False)
        if "595" in text:
            checks.append("fix_mentioned")
        passed = "595" in text
        return {"exec_pass": passed, "exec_error": None if passed else "fix_incomplete", "checks": checks}

    if tid == "slide_outline_json":
        blob = _extract_json_blob(output)
        ok, err, slide_checks = _verify_slide_outline(blob)
        checks.extend(slide_checks)
        return {"exec_pass": ok, "exec_error": err, "checks": checks}

    if tid == "proposal_json":
        blob = _extract_json_blob(output)
        ok, err, prop_checks = _verify_proposal_json(blob)
        checks.extend(prop_checks)
        return {"exec_pass": ok, "exec_error": err, "checks": checks}

    if tid == "ad_copy_json":
        blob = _extract_json_blob(output)
        ok, err, ad_checks = _verify_ad_copy_json(blob)
        checks.extend(ad_checks)
        return {"exec_pass": ok, "exec_error": err, "checks": checks}

    if tid == "slide_deck_create":
        paths = list(artifacts or [])
        if sandbox_dir and sandbox_dir.is_dir():
            paths.extend(str(p) for p in sandbox_dir.rglob("*.pptx"))
        paths = list(dict.fromkeys(paths))
        if not paths:
            return {"exec_pass": False, "exec_error": "no_pptx_artifact", "checks": checks}
        ok, err, pptx_checks, artifact = _verify_pptx_deck(paths)
        checks.extend(pptx_checks)
        out: dict = {"exec_pass": ok, "exec_error": err, "checks": checks}
        if artifact:
            out["artifact"] = artifact
        return out

    src = task.get("source") or task.get("suite") or ""
    if src in ("presentbench", "slidesgen", "deckbench") or tid.startswith(
        ("ext_presentbench_", "ext_slidesgen_", "ext_deckbench_")
    ):
        paths = list(artifacts or [])
        if sandbox_dir and sandbox_dir.is_dir():
            paths.extend(str(p) for p in sandbox_dir.rglob("*.pptx"))
        paths = list(dict.fromkeys(paths))
        if src == "presentbench" or tid.startswith("ext_presentbench_"):
            return verify_presentbench_checklist(task, output, paths)
        if not paths:
            return {"exec_pass": False, "exec_error": "no_pptx_artifact", "checks": checks}
        min_slides = int(task.get("min_slides") or 5)
        ok, err, pptx_checks, artifact = _verify_pptx_deck(paths, min_slides=min_slides)
        checks.extend(pptx_checks)
        out = {"exec_pass": ok, "exec_error": err, "checks": checks}
        if artifact:
            out["artifact"] = artifact
        return out

    if src == "officeeval" or tid.startswith("ext_officeeval_"):
        paths = list(artifacts or [])
        if sandbox_dir and sandbox_dir.is_dir():
            paths.extend(str(p) for p in sandbox_dir.rglob("*.xlsx"))
        return verify_officeeval_spec(task, output, paths)

    if src == "adbench" or tid.startswith("ext_adbench_"):
        return verify_adbench_gt(task, output)

    if src == "bizgeneval" or tid.startswith("ext_bizgeneval_"):
        return verify_bizgeneval_json(task, output)

    return {"exec_pass": None, "checks": []}


def office_task_needs_verify(task: dict) -> bool:
    verify = task.get("verify")
    if verify == "office":
        return True
    if verify == "none":
        return False
    src = task.get("source") or task.get("suite") or ""
    if src in ("presentbench", "slidesgen", "officeeval", "adbench", "bizgeneval", "odysseybench"):
        return task.get("verify") == "office"
    return task.get("id") in (
        "excel_calc", "excel_create_e2e", "excel_read_fix", "python_calc_xlsx", "excel_read_fix_save",
        "slide_outline_json", "slide_deck_create", "proposal_json", "ad_copy_json",
    ) or task.get("id", "").startswith(
        ("ext_presentbench_", "ext_slidesgen_", "ext_officeeval_", "ext_adbench_", "ext_bizgeneval_")
    )


def task_is_verify_first(task: dict) -> bool:
    if task.get("id") in VERIFY_FIRST_TASKS:
        return True
    src = task.get("source") or task.get("suite") or ""
    if src in ("humaneval", "mbpp", "bizgeneval", "officeeval", "presentbench", "slidesgen"):
        return True
    tid = task.get("id", "")
    return tid.startswith(("ext_humaneval_", "ext_mbpp_", "ext_bizgeneval_", "ext_officeeval_"))


def merge_pass(
    task: dict,
    *,
    judge_pass: bool,
    verify: dict | None,
    subjective: bool = True,
) -> bool:
    if verify and verify.get("exec_pass") is False:
        return False
    if verify and verify.get("exec_pass") is True and task_is_verify_first(task):
        return True
    if not subjective:
        return verify.get("exec_pass") is True if verify else False
    return judge_pass


def _unique_tool_names(trace: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in trace:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def verify_tool_use(
    task: dict,
    stats: dict | None = None,
    tool_trace: list[str] | None = None,
) -> dict:
    """Agent 태스크 도구 호출 요건 검증 — required/forbidden/min·max rounds."""
    raw = tool_trace if tool_trace is not None else (stats or {}).get("tools_called") or []
    tools_called = _unique_tool_names(list(raw))

    required = list(task.get("required_tools") or [])
    forbidden = list(task.get("forbidden_tools") or [])
    if not forbidden and task.get("category") == "office":
        forbidden = list(OFFICE_FORBIDDEN_TOOLS)

    min_rounds = int(task.get("min_tool_rounds") or 0)
    max_rounds = task.get("max_tool_rounds")
    tool_rounds = int((stats or {}).get("tool_rounds") or 0)

    missing = [t for t in required if t not in tools_called]
    forbidden_used = [t for t in tools_called if t in forbidden]

    rounds_ok = tool_rounds >= min_rounds
    if max_rounds is not None and tool_rounds > int(max_rounds):
        rounds_ok = False

    required_met = not missing if required else True
    forbidden_ok = not forbidden_used
    tool_pass = required_met and forbidden_ok and rounds_ok

    if not required:
        tool_score = 1.0 if forbidden_ok and rounds_ok else 0.0
    else:
        hit = len(required) - len(missing)
        tool_score = round(hit / len(required), 3)
        if tool_pass:
            tool_score = 1.0

    return {
        "tools_called": tools_called,
        "required_tools": required,
        "required_tools_met": required_met,
        "missing_tools": missing,
        "forbidden_tools_used": forbidden_used,
        "tool_rounds": tool_rounds,
        "min_tool_rounds": min_rounds,
        "tool_rounds_ok": rounds_ok,
        "tool_score": tool_score,
        "tool_pass": tool_pass,
    }


def merge_agent_pass(
    task: dict,
    *,
    judge_pass: bool,
    verify: dict | None,
    tool_verify: dict | None,
    subjective: bool = True,
) -> bool:
    passed = merge_pass(task, judge_pass=judge_pass, verify=verify, subjective=subjective)
    if not passed:
        return False
    if not tool_verify:
        return True
    explicit = bool(
        task.get("required_tools")
        or task.get("forbidden_tools")
        or task.get("min_tool_rounds")
        or task.get("max_tool_rounds")
    )
    if explicit:
        return bool(tool_verify.get("tool_pass"))
    if tool_verify.get("forbidden_tools_used"):
        return False
    return True


def load_tasks_from_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def load_tasks_from_manifest(
    *,
    suites: list[str] | None = None,
    routing_only: bool = True,
    harness: str | None = None,
    manifest_path: Path = MANIFEST_PATH,
) -> list[dict]:
    if not manifest_path.is_file():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = suites or manifest.get("routing_suites") or []
    if len(names) == 1 and names[0] == "routing":
        names = manifest.get("routing_suites") or []
    out: list[dict] = []
    for name in names:
        for t in load_tasks_from_jsonl(EXTERNAL_ROOT / name / "tasks.jsonl"):
            if routing_only and not t.get("routing_default", True):
                continue
            th = t.get("harness", "smoke")
            if harness == "smoke" and th not in ("smoke", "both"):
                continue
            if harness == "agent" and th not in ("agent", "both"):
                continue
            out.append(t)
    return out


def load_tasks(
    path: Path = TASKS_PATH,
    categories: list[str] | None = None,
    harness: str | None = None,
    *,
    include_external: bool = False,
    external_suites: list[str] | None = None,
) -> list[dict]:
    if path == MANIFEST_PATH or str(path).endswith("manifest.json"):
        return load_tasks_from_manifest(suites=external_suites, harness=harness)
    if path.suffix == ".jsonl":
        tasks = load_tasks_from_jsonl(path)
        if categories:
            tasks = [t for t in tasks if t.get("category") in categories]
        if harness == "smoke":
            tasks = [t for t in tasks if t.get("harness", "smoke") in ("smoke", "both")]
        elif harness == "agent":
            tasks = [t for t in tasks if t.get("harness") in ("agent", "both")]
        return tasks
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cats = data.get("categories", {})
    out: list[dict] = []
    for cat, tasks in cats.items():
        if categories and cat not in categories:
            continue
        for t in tasks:
            th = t.get("harness", "smoke")
            if harness == "smoke" and th not in ("smoke", "both"):
                continue
            if harness == "agent" and th not in ("agent", "both"):
                continue
            out.append({**t, "category": cat})
    if include_external:
        ext = load_tasks_from_manifest(suites=external_suites, harness=harness)
        if categories:
            ext = [t for t in ext if t.get("category") in categories]
        out.extend(ext)
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


def resolve_judge_backend() -> str:
    """VEGA_BENCH_JUDGE=claude-cli → Claude Code `claude -p` (subscription, no OR cost)."""
    backend = (os.getenv("VEGA_BENCH_JUDGE") or "openrouter").strip().lower()
    if backend in ("claude", "claude-cli", "claude_p", "claude-p"):
        return "claude-cli"
    return "openrouter"


def _claude_cli_chat(system: str, user: str, *, timeout_sec: int = 180) -> dict:
    """Non-interactive judge via `claude -p` (Claude Code quota)."""
    prompt = f"{system}\n\n---\n\n{user}" if system else user
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(REPO),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {"text": "", "tokens_in": 0, "tokens_out": 0, "latency_sec": timeout_sec,
                "error": "claude_cli_timeout"}
    latency = round(time.monotonic() - t0, 2)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[:300]
        return {"text": "", "tokens_in": 0, "tokens_out": 0, "latency_sec": latency,
                "error": f"claude_cli_exit_{proc.returncode}:{err}"}
    text = proc.stdout or ""
    # Strip common warning line from claude -p
    lines = [ln for ln in text.splitlines()
             if not ln.startswith("Warning: no stdin data received")]
    text = "\n".join(lines).strip()
    return {"text": text, "tokens_in": 0, "tokens_out": 0, "latency_sec": latency}


def _or_chat(model: str, messages: list[dict], key: str, max_tokens: int = 1500) -> dict:
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(
        _OR_URL, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/unohee/VEGA",
            "X-Title": "VEGA-bench",
        },
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())
    latency = round(time.monotonic() - t0, 2)
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    usage = data.get("usage") or {}
    return {
        "text": text,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
        "latency_sec": latency,
    }


def _parse_judge(raw: str) -> list[dict]:
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
    total = sum(min(2, max(0, int(x.get("score", 0)))) for x in scores)
    mx = 2 * len(scores)
    ratio = round(total / mx, 3) if mx else 0.0
    return {"total": total, "max": mx, "ratio": ratio, "pass": bool(mx) and ratio >= 0.7}


def judge(
    task: dict,
    output: str,
    key: str,
    judge_model: str = JUDGE_MODEL,
    *,
    judge_backend: str | None = None,
) -> dict:
    rubric = "\n".join(f"{i+1}. {c}" for i, c in enumerate(task["rubric"]))
    sys = (
        "너는 엄격한 평가자다. 아래 과제 출력이 각 rubric 항목을 얼마나 충족하는지 "
        "0(미충족)·1(부분)·2(충족)으로 채점하라. 반드시 JSON 배열만 출력: "
        '[{"criterion":"...","score":0,"note":"..."}]'
    )
    usr = f"[과제]\n{task['prompt']}\n\n[출력]\n{output}\n\n[rubric]\n{rubric}"
    backend = judge_backend or resolve_judge_backend()
    if backend == "claude-cli":
        res = _claude_cli_chat(sys, usr)
    else:
        if not key:
            return {"ratio": 0.0, "pass": False, "scores": [], "error": "no_openrouter_key"}
        res = _or_chat(judge_model, [{"role": "system", "content": sys},
                                     {"role": "user", "content": usr}], key, max_tokens=900)
    if res.get("error"):
        return {"ratio": 0.0, "pass": False, "scores": [], "error": res["error"],
                "judge_backend": backend}
    scores = _parse_judge(res["text"])
    agg = aggregate(scores)
    if not scores:
        agg["error"] = "judge_parse_failed"
        agg["judge_raw"] = res["text"][:300]
    agg["judge_backend"] = backend
    return {**agg, "scores": scores}


def run_smoke_task(
    model: str,
    task: dict,
    key: str,
    *,
    sandbox_dir: Path | None = None,
) -> dict:
    prompt = task_prompt(task, harness="smoke")
    try:
        run = _or_chat(model, [{"role": "user", "content": prompt}], key)
    except Exception as e:
        return {"model": model, "task": task["id"], "category": task["category"],
                "harness": "smoke", "error": str(e)[:200]}

    verify_first = task_is_verify_first(task)
    skip_judge = verify_first and (
        task["category"] == "swe" or office_task_needs_verify(task)
    )
    verdict = (
        {"ratio": 0.0, "pass": False, "scores": []}
        if skip_judge
        else judge(task, run["text"], key)
    )

    verify: dict = {}
    if task["category"] == "swe":
        verify = verify_swe(task, run["text"])
    elif office_task_needs_verify(task):
        sd = sandbox_dir or REPO / "build_output" / "bench_verify" / model.replace("/", "_")
        verify = verify_office(task, run["text"], sandbox_dir=sd)

    if verify_first and verify.get("exec_pass") is True:
        verdict["ratio"] = 1.0
        verdict["pass"] = True
    subjective = not verify_first
    passed = merge_pass(task, judge_pass=verdict["pass"], verify=verify or None, subjective=subjective)

    out: dict = {
        "model": model, "task": task["id"], "category": task["category"], "harness": "smoke",
        "tokens_in": run["tokens_in"], "tokens_out": run["tokens_out"],
        "latency_sec": run["latency_sec"], "tool_call_count": 0, "tool_rounds": 0,
        "ratio": verdict["ratio"], "pass": passed, "scores": verdict["scores"],
    }
    if verify:
        out["verify"] = verify
        out["exec_pass"] = verify.get("exec_pass")
    if verdict.get("error"):
        out["error"] = verdict["error"]
        if verdict.get("judge_raw"):
            out["judge_raw"] = verdict["judge_raw"]
    if task["category"] == "swe" and verify.get("exec_pass") is False:
        out["pass"] = False
    elif task["category"] == "swe" and verify_first and verify.get("exec_pass") is True:
        out["pass"] = True
        out["ratio"] = 1.0
    if task.get("source"):
        out["source"] = task["source"]
    if task.get("suite"):
        out["suite"] = task["suite"]
    if task.get("id") == "lang_hallucination_guard":
        out["hallucination_chars_detected"] = detect_cjk_hallucination(run["text"])
        if out["hallucination_chars_detected"]:
            out["pass"] = False
    return out


def summarize_results(results: list[dict]) -> tuple[dict, dict, dict, dict]:
    summary: dict = {}
    by_cat: dict[str, dict] = {}
    by_task: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    for r in results:
        s = summary.setdefault(r["model"], {"pass": 0, "n": 0, "tok_out": 0, "lat": 0.0, "ratios": []})
        s["n"] += 1
        s["pass"] += 1 if r.get("pass") else 0
        s["tok_out"] += r.get("tokens_out", 0)
        s["lat"] += r.get("latency_sec", 0.0)
        if "ratio" in r:
            s["ratios"].append(float(r["ratio"]))
        cat = r.get("category", "unknown")
        cs = by_cat.setdefault(cat, {"pass": 0, "n": 0, "ratios": []})
        cs["n"] += 1
        cs["pass"] += 1 if r.get("pass") else 0
        if "ratio" in r:
            cs["ratios"].append(float(r["ratio"]))
        tid = r.get("task", "unknown")
        ts = by_task.setdefault(tid, {"pass": 0, "n": 0, "ratios": []})
        ts["n"] += 1
        ts["pass"] += 1 if r.get("pass") else 0
        if "ratio" in r:
            ts["ratios"].append(float(r["ratio"]))
        src = r.get("source") or r.get("suite") or "native"
        ss = by_source.setdefault(src, {"pass": 0, "n": 0, "ratios": []})
        ss["n"] += 1
        ss["pass"] += 1 if r.get("pass") else 0
        if "ratio" in r:
            ss["ratios"].append(float(r["ratio"]))
    for s in summary.values():
        rs = s.pop("ratios", [])
        s["mean_ratio"] = round(sum(rs) / len(rs), 3) if rs else 0.0
    summary_by_category = {
        cat: {"pass": v["pass"], "n": v["n"],
              "mean_ratio": round(sum(v["ratios"]) / len(v["ratios"]), 3) if v["ratios"] else 0.0}
        for cat, v in by_cat.items()
    }
    summary_by_task = {
        tid: {"pass": v["pass"], "n": v["n"],
              "mean_ratio": round(sum(v["ratios"]) / len(v["ratios"]), 3) if v["ratios"] else 0.0}
        for tid, v in by_task.items()
    }
    summary_by_source = {
        src: {"pass": v["pass"], "n": v["n"],
              "mean_ratio": round(sum(v["ratios"]) / len(v["ratios"]), 3) if v["ratios"] else 0.0}
        for src, v in by_source.items()
    }
    return summary, summary_by_category, summary_by_task, summary_by_source


def build_artifact(results: list[dict], *, harness: str, judge_model: str = JUDGE_MODEL) -> dict:
    summary, summary_by_category, summary_by_task, summary_by_source = summarize_results(results)
    return {
        "schema_version": 2,
        "harness": harness,
        "judge_model": judge_model,
        "judge_backend": resolve_judge_backend(),
        "results": results,
        "summary": summary,
        "summary_by_category": summary_by_category,
        "summary_by_task": summary_by_task,
        "summary_by_source": summary_by_source,
    }






def load_excel_winners(*, full_only: bool = True) -> list[str]:
    """Excel 벤치 full pass 모델 (@excel). data/bench_excel_winners.json."""
    if not EXCEL_WINNERS_PATH.is_file():
        return []
    try:
        data = json.loads(EXCEL_WINNERS_PATH.read_text(encoding="utf-8"))
        rows = data.get("full_excel_winners") or []
        return [r["id"] for r in rows]
    except Exception:
        return []


def load_tier1_models(path: Path = TIER1_PATH) -> list[str]:
    """1차 bench 7/7 상위 모델 (@tier1)."""
    if not path.is_file():
        return [
            "google/gemini-2.5-flash-lite-preview-09-2025",
            "openai/gpt-4o-mini",
            "deepseek/deepseek-v3.1-terminus",
            "deepseek/deepseek-v3.2-exp",
        ]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("models") or [])
    except Exception:
        return []


def resolve_curated_models() -> list[str]:
    try:
        from pipeline.model_catalog import curate_models
        from web.routers.llm import _fetch_models
        return [m["id"] for m in curate_models(_fetch_models("openrouter"))]
    except Exception:
        return []


def parse_models_arg(raw: str) -> list[str]:
    key = raw.strip()
    if key == "@excel":
        ids = load_excel_winners()
        return ids if ids else load_tier1_models()
    if key == "@tier1":
        ids = load_tier1_models()
        return ids if ids else ["openai/gpt-4o-mini"]
    if key == "@curated":
        ids = resolve_curated_models()
        return ids if ids else ["deepseek/deepseek-v4-flash"]
    return [m.strip() for m in raw.split(",") if m.strip()]
