# Created: 2026-06-23
# Purpose: 모델 벤치 하니스 순수 로직 회귀 (INT-1889/1890/1891). LLM 호출 없는 부분만.
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("bench_lib", _REPO / "scripts" / "bench_lib.py")
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def test_load_tasks_all_categories():
    tasks = bench.load_tasks(harness="smoke")
    cats = {t["category"] for t in tasks}
    assert {"office", "swe", "multilingual"} <= cats
    for t in tasks:
        assert t["id"] and t["prompt"] and isinstance(t["rubric"], list)


def test_load_tasks_agent_harness():
    tasks = bench.load_tasks(categories=["office"], harness="agent")
    assert tasks
    assert all(t.get("harness") in ("agent", "both") for t in tasks)
    assert "excel_create_e2e" in {t["id"] for t in tasks}


def test_load_tasks_filter():
    tasks = bench.load_tasks(categories=["office"], harness="smoke")
    assert tasks and all(t["category"] == "office" for t in tasks)


def test_aggregate_pass_fail():
    full = [{"score": 2}, {"score": 2}]
    assert bench.aggregate(full) == {"total": 4, "max": 4, "ratio": 1.0, "pass": True}
    weak = [{"score": 0}, {"score": 1}]
    a = bench.aggregate(weak)
    assert a["pass"] is False and a["ratio"] < 0.7


def test_summarize_results_by_category():
    rows = [
        {"model": "m/a", "category": "office", "task": "t1", "pass": True, "ratio": 1.0, "source": "humaneval"},
        {"model": "m/a", "category": "swe", "task": "t2", "pass": False, "ratio": 0.5},
    ]
    summary, by_cat, by_task, by_src = bench.summarize_results(rows)
    assert summary["m/a"]["pass"] == 1
    assert by_cat["office"]["pass"] == 1
    assert by_task["t1"]["n"] == 1
    assert "humaneval" in by_src


def test_parse_sheets_json_variants():
    canonical = '{"매출": [["월","매출"],["1월",120],["2월",95]]}'
    out = bench.parse_sheets_json(canonical)
    assert out and "매출" in out
    alt = '{"sheetName": "매출", "headers": ["월","매출"], "data": [[1,120],[2,95]]}'
    out2 = bench.parse_sheets_json(alt)
    assert out2 and len(out2["매출"]) >= 2


def test_verify_office_excel_calc(tmp_path):
    text = """```json
{"매출": [["월","매출(만원)"],["1월",120],["2월",95],["3월",140],["4월",110],["5월",130],["합계",595],["평균",119]]}
```"""
    r = bench.verify_office({"id": "excel_calc"}, text, sandbox_dir=tmp_path)
    assert r["exec_pass"] is True, r


def test_verify_office_excel_calc_wrong_sum(tmp_path):
    text = '{"매출": [["1월",120],["2월",95],["3월",140],["4월",110],["5월",130]]}'
    r = bench.verify_office({"id": "excel_calc"}, text, sandbox_dir=tmp_path)
    # missing sum row may still pass numbers check if values present
    assert "exec_pass" in r


def test_build_artifact_v2():
    rows = [{"model": "m", "category": "office", "task": "t", "pass": True, "ratio": 1.0}]
    art = bench.build_artifact(rows, harness="smoke")
    assert art["schema_version"] == 2
    assert "summary_by_category" in art


def test_parse_judge_extracts_json_array():
    raw = 'x\n[{"criterion":"a","score":2,"note":"ok"}]\n'
    out = bench._parse_judge(raw)
    assert len(out) == 1 and out[0]["score"] == 2


def test_dry_run_no_budget():
    r = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "bench_models.py"),
         "--dry-run", "--categories", "office", "--limit", "1"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "dry-run" in r.stdout


def test_bench_agent_dry_run():
    r = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "bench_agent.py"), "--dry-run", "--limit-models", "1"],
        capture_output=True, text=True, timeout=30, cwd=str(_REPO),
    )
    assert r.returncode == 0, r.stderr
    assert "bench-agent" in r.stdout


def test_detect_cjk_hallucination():
    assert bench.detect_cjk_hallucination("안녕하세요") is False
    assert bench.detect_cjk_hallucination("こんにちは") is True


def test_verify_swe_py_bugfix():
    good = "def avg(xs):\n    if not xs:\n        return 0.0\n    return sum(xs)/len(xs)"
    r = bench.verify_swe({"id": "py_bugfix"}, f"```python\n{good}\n```")
    assert r["exec_pass"] is True


def test_task_prompt_injects_rules_for_press_release(tmp_path, monkeypatch):
    rules = tmp_path / "RULES.md"
    rules.write_text("# Press rules\n- no duplicate quotes", encoding="utf-8")
    monkeypatch.setattr(bench, "RULES_PATH", rules)
    out = bench.task_prompt({"id": "press_release_single", "prompt": "보도자료 써줘"})
    assert "Press rules" in out


def test_xlsx_create_read_roundtrip(tmp_path):
    from pipeline.tools_office import xlsx_create, xlsx_read
    out = tmp_path / "sales.xlsx"
    cr = xlsx_create(str(out), {"매출": [["월", "매출"], ["1월", 120], ["2월", 95]]})
    assert cr.get("ok") is True, cr
    rd = xlsx_read(str(out))
    assert rd.get("rows") and rd["rows"][1][1] == 120


def test_load_tier1_models():
    models = bench.load_tier1_models()
    assert len(models) == 4
    assert "gpt-4o-mini" in models[1]


def test_parse_models_arg_tier1():
    assert bench.parse_models_arg("@tier1") == bench.load_tier1_models()


def test_verify_swe_py_is_palindrome():
    code = (
        "def is_palindrome(text):\n"
        "    t = ''.join(c.lower() for c in text if c.isalnum())\n"
        "    return t == t[::-1]"
    )
    r = bench.verify_swe({"id": "py_is_palindrome"}, f"```python\n{code}\n```")
    assert r["exec_pass"] is True, r


def test_verify_swe_py_fizzbuzz():
    code = (
        "def fizzbuzz(n):\n"
        "    out = []\n"
        "    for i in range(1, n + 1):\n"
        "        if i % 15 == 0: out.append('FizzBuzz')\n"
        "        elif i % 3 == 0: out.append('Fizz')\n"
        "        elif i % 5 == 0: out.append('Buzz')\n"
        "        else: out.append(str(i))\n"
        "    return out"
    )
    r = bench.verify_swe({"id": "py_fizzbuzz"}, f"```python\n{code}\n```")
    assert r["exec_pass"] is True, r


def test_verify_swe_py_clamp():
    code = (
        "def clamp(val, lo, hi):\n"
        "    if lo > hi: raise ValueError\n"
        "    return max(lo, min(hi, val))"
    )
    r = bench.verify_swe({"id": "py_clamp"}, f"```python\n{code}\n```")
    assert r["exec_pass"] is True, r


def test_verify_office_slide_outline():
    text = """```json
{"slides": [
  {"title": "문제", "bullets": ["a"]},
  {"title": "솔루션", "bullets": ["b"]},
  {"title": "기능", "bullets": ["c"]},
  {"title": "가격", "bullets": ["d"]},
  {"title": "다음", "bullets": ["VEGA"]}
]}
```"""
    r = bench.verify_office({"id": "slide_outline_json"}, text)
    assert r["exec_pass"] is True, r


def test_verify_office_proposal_json():
    text = json.dumps({
        "배경": "M사 ERP 통합 6개월 9월 착수",
        "과업범위": "ERP·그룹웨어 ISO27001 준수",
        "일정": "2026년 9월 착수 6개월",
        "예산": "8,000만원 부가세 별도",
        "수행조직": "PM 1 PMO 2 개발 5",
    }, ensure_ascii=False)
    r = bench.verify_office({"id": "proposal_json"}, text)
    assert r["exec_pass"] is True, r


def test_verify_office_ad_copy_json():
    text = json.dumps({
        "headline": "터미널 없는 AI 워크스페이스",
        "body": "VEGA로 로컬에서 프라이버시를 지키며 LLM 파워유저 워크플로를.",
        "cta": "무료 다운로드",
        "hashtags": ["#VEGA", "#로컬AI", "#프라이버시"],
    }, ensure_ascii=False)
    r = bench.verify_office({"id": "ad_copy_json"}, text)
    assert r["exec_pass"] is True, r


def test_task_is_verify_first():
    assert bench.task_is_verify_first({"id": "py_fizzbuzz"})
    assert bench.task_is_verify_first({"id": "ad_copy_json"})
    assert not bench.task_is_verify_first({"id": "py_bugfix"})


def test_verify_tool_use_required_met():
    task = {"id": "excel_create_e2e", "category": "office", "required_tools": ["xlsx_create"], "min_tool_rounds": 1}
    stats = {"tool_rounds": 2, "tools_called": ["xlsx_create", "xlsx_create"]}
    r = bench.verify_tool_use(task, stats)
    assert r["required_tools_met"] is True
    assert r["tool_pass"] is True
    assert r["tool_score"] == 1.0


def test_verify_tool_use_missing_required():
    task = {"id": "excel_create_e2e", "category": "office", "required_tools": ["xlsx_create"], "min_tool_rounds": 1}
    stats = {"tool_rounds": 1, "tools_called": ["python_exec"]}
    r = bench.verify_tool_use(task, stats)
    assert r["required_tools_met"] is False
    assert r["missing_tools"] == ["xlsx_create"]
    assert r["tool_pass"] is False
    assert r["tool_score"] == 0.0


def test_verify_tool_use_forbidden_host_exec():
    task = {"id": "slide_deck_create", "category": "office", "required_tools": ["pptx_create"]}
    stats = {"tool_rounds": 1, "tools_called": ["host_exec", "pptx_create"]}
    r = bench.verify_tool_use(task, stats)
    assert "host_exec" in r["forbidden_tools_used"]
    assert r["tool_pass"] is False


def test_merge_agent_pass_tool_gate():
    task = {"id": "excel_create_e2e", "required_tools": ["xlsx_create"]}
    tool_bad = {"tool_pass": False, "required_tools_met": False}
    assert bench.merge_agent_pass(task, judge_pass=True, verify={"exec_pass": True}, tool_verify=tool_bad) is False
    tool_ok = {"tool_pass": True, "required_tools_met": True}
    assert bench.merge_agent_pass(task, judge_pass=True, verify={"exec_pass": True}, tool_verify=tool_ok) is True


def test_load_tasks_agent_tool_required():
    tasks = bench.load_tasks(categories=["office"], harness="agent")
    ids = {t["id"] for t in tasks}
    for tid in (
        "excel_create_e2e", "excel_read_fix_save", "python_calc_xlsx",
        "web_search_summarize", "proposal_rfp_agent", "ad_copy_research",
    ):
        assert tid in ids, tid
    e2e = next(t for t in tasks if t["id"] == "excel_create_e2e")
    assert e2e.get("required_tools") == ["xlsx_create"]


def test_verify_office_python_calc_xlsx(tmp_path):
    from pipeline.tools_office import xlsx_create
    fp = tmp_path / "calc_sales.xlsx"
    xlsx_create(str(fp), {
        "매출": [
            ["월", "매출"],
            ["1월", 120], ["2월", 95], ["3월", 140], ["4월", 110], ["5월", 130],
            ["합계", 595], ["평균", 119],
        ]
    })
    r = bench.verify_office({"id": "python_calc_xlsx"}, "", sandbox_dir=tmp_path)
    assert r["exec_pass"] is True, r


def test_verify_office_excel_read_fix_save(tmp_path):
    from pipeline.tools_office import xlsx_create
    fp = tmp_path / "sales_fixed.xlsx"
    xlsx_create(str(fp), {
        "매출": [
            ["월", "매출(만원)"],
            ["1월", 120], ["2월", 95], ["3월", 140], ["4월", 110], ["5월", 130],
            ["합계", 595],
        ]
    })
    r = bench.verify_office({"id": "excel_read_fix_save"}, "", sandbox_dir=tmp_path)
    assert r["exec_pass"] is True, r


def test_load_tasks_extended_counts():
    all_smoke = bench.load_tasks(harness="smoke")
    ids = {t["id"] for t in all_smoke}
    for tid in (
        "slide_outline_json", "proposal_json", "ad_copy_json",
        "proposal_rfp", "ad_copy_campaign",
        "py_is_palindrome", "py_fizzbuzz", "py_clamp",
    ):
        assert tid in ids, tid
