# Created: 2026-06-25
# Purpose: External bench ingest/load/verify unit tests (no API).
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("bench_lib", _REPO / "scripts" / "bench_lib.py")
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def test_manifest_exists():
    assert bench.MANIFEST_PATH.is_file()
    data = json.loads(bench.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert "routing_suites" in data
    assert len(data["routing_suites"]) >= 10


def test_load_tasks_from_manifest_humaneval():
    tasks = bench.load_tasks_from_manifest(suites=["humaneval"], harness="smoke")
    assert len(tasks) >= 1
    t = tasks[0]
    assert t["id"].startswith("ext_humaneval_")
    assert t.get("entry_point")
    assert t.get("test")


def test_verify_code_harness_clamp():
    task = {
        "id": "ext_humaneval_test",
        "source": "humaneval",
        "entry_point": "clamp",
        "test": (
            "def check(candidate):\n"
            "    assert candidate(5, 0, 10) == 5\n"
            "    assert candidate(-1, 0, 10) == 0\n"
        ),
    }
    output = "```python\ndef clamp(n, lo, hi):\n    if lo > hi:\n        raise ValueError\n    return max(lo, min(hi, n))\n```"
    v = bench.verify_code_harness(task, output)
    assert v["exec_pass"] is True


def test_verify_bizgeneval_json():
    task = {"id": "ext_bizgeneval_000", "source": "bizgeneval", "bizgeneval_keys": ["title"]}
    out = '{"title": "VEGA", "bullets": ["a", "b"]}'
    v = bench.verify_bizgeneval_json(task, out)
    assert v["exec_pass"] is True


def test_build_artifact_summary_by_source():
    rows = [
        {"model": "m/a", "category": "swe", "task": "ext_humaneval_000", "source": "humaneval",
         "pass": True, "ratio": 1.0, "harness": "smoke"},
        {"model": "m/a", "category": "office", "task": "press_release", "pass": False,
         "ratio": 0.5, "harness": "smoke"},
    ]
    art = bench.build_artifact(rows, harness="external")
    assert "summary_by_source" in art
    assert art["summary_by_source"]["humaneval"]["pass"] == 1


def test_external_routing_task_count():
    tasks = bench.load_tasks_from_manifest(routing_only=True)
    assert len(tasks) >= 100


def test_resolve_judge_backend_default():
    import os
    os.environ.pop("VEGA_BENCH_JUDGE", None)
    assert bench.resolve_judge_backend() == "openrouter"
    os.environ["VEGA_BENCH_JUDGE"] = "claude-cli"
    assert bench.resolve_judge_backend() == "claude-cli"
    os.environ.pop("VEGA_BENCH_JUDGE", None)


def test_mbpp_assert_line_no_double_assert():
    import importlib.util as iu
    spec = iu.spec_from_file_location("ingest", _REPO / "scripts" / "bench_external" / "ingest.py")
    ingest = iu.module_from_spec(spec)
    spec.loader.exec_module(ingest)
    raw = "assert set(similar_elements((1, 2), (2, 3))) == set((2,))"
    line = ingest._mbpp_assert_line(raw)
    assert line == raw
    compile(line, "<mbpp>", "exec")


def test_task_is_verify_first_swebench_lite():
    task = {"id": "ext_swebench_lite_000", "source": "swebench_lite"}
    assert bench.task_is_verify_first(task)
    merged = bench.merge_pass(
        task, judge_pass=False, verify={"exec_pass": True}, subjective=True,
    )
    assert merged is True


def test_verify_tool_use_xlsx_read_alias():
    task = {
        "id": "excel_read_fix_save",
        "category": "office",
        "required_tools": ["xlsx_read", "xlsx_create"],
        "min_tool_rounds": 2,
    }
    stats = {"tools_called": ["file_read", "xlsx_create"], "tool_rounds": 2}
    tv = bench.verify_tool_use(task, stats)
    assert tv["tool_pass"] is True
    assert tv["missing_tools"] == []


def test_merge_external_artifact_shape():
    import importlib.util as iu
    spec = iu.spec_from_file_location("merge", _REPO / "scripts" / "merge_bench_artifacts.py")
    merge_mod = iu.module_from_spec(spec)
    spec.loader.exec_module(merge_mod)
    rows = [{"model": "m", "task": "ext_mbpp_000", "category": "swe", "source": "mbpp",
             "pass": True, "ratio": 1.0, "harness": "smoke"}]
    art = bench.build_artifact(rows, harness="external")
    tmp = _REPO / "build_output" / "test_ext_merge.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(art), encoding="utf-8")
    merged = merge_mod.merge(_REPO / "build_output" / "nonexistent.json", None, tmp)
    assert merged.get("summary_by_source")
    assert len(merged["results"]) == 1
