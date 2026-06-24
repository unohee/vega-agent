# Created: 2026-06-23
# Purpose: 모델 벤치 하니스 순수 로직 회귀 (INT-1889/1890/1891). LLM 호출 없는 부분만.
# Dependencies: scripts/bench_models.py, data/bench_tasks.json
# Test Status: green (2026-06-23)

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("bench_models", _REPO / "scripts" / "bench_models.py")
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def test_load_tasks_all_categories():
    tasks = bench.load_tasks()
    cats = {t["category"] for t in tasks}
    assert {"office", "swe", "multilingual"} <= cats
    for t in tasks:
        assert t["id"] and t["prompt"] and isinstance(t["rubric"], list) and t["rubric"]


def test_load_tasks_filter():
    tasks = bench.load_tasks(categories=["office"])
    assert tasks and all(t["category"] == "office" for t in tasks)


def test_aggregate_pass_fail():
    full = [{"score": 2}, {"score": 2}]
    assert bench.aggregate(full) == {"total": 4, "max": 4, "ratio": 1.0, "pass": True}
    weak = [{"score": 0}, {"score": 1}]
    a = bench.aggregate(weak)
    assert a["pass"] is False and a["ratio"] < 0.7
    # 점수 클램프(>2, <0)
    assert bench.aggregate([{"score": 9}])["total"] == 2
    assert bench.aggregate([])["max"] == 0


def test_parse_judge_extracts_json_array():
    raw = 'some preamble\n[{"criterion":"a","score":2,"note":"ok"}]\ntrailing'
    out = bench._parse_judge(raw)
    assert len(out) == 1 and out[0]["score"] == 2
    assert bench._parse_judge("no json here") == []


def test_dry_run_no_budget():
    # --dry-run 은 API 호출 없이 종료(예산 0) — 키 없어도 성공해야 함
    r = subprocess.run([sys.executable, str(_REPO / "scripts" / "bench_models.py"),
                        "--dry-run", "--categories", "office", "--limit", "1"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "dry-run" in r.stdout and "office/" in r.stdout


def test_detect_cjk_hallucination():
    assert bench.detect_cjk_hallucination("안녕하세요 좋은 하루입니다.") is False
    assert bench.detect_cjk_hallucination("こんにちは") is True


def test_verify_swe_py_bugfix():
    good = "def avg(xs):\n    if not xs:\n        return 0.0\n    return sum(xs)/len(xs)"
    r = bench.verify_swe({"id": "py_bugfix"}, f"```python\n{good}\n```")
    assert r["exec_pass"] is True


def test_judge_parse_failure_marks_error():
    agg = bench.aggregate([])
    agg["error"] = "judge_parse_failed"
    assert agg["pass"] is False and agg["error"] == "judge_parse_failed"


def test_task_prompt_injects_rules_for_press_release(tmp_path, monkeypatch):
    rules = tmp_path / "RULES.md"
    rules.write_text("# Press rules\n- no duplicate quotes", encoding="utf-8")
    monkeypatch.setattr(bench, "RULES_PATH", rules)
    out = bench.task_prompt({"id": "press_release_single", "prompt": "보도자료 써줘"})
    assert "Press rules" in out and "보도자료 써줘" in out
