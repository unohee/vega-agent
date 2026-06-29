#!/usr/bin/env python3
# PresentBench-style checklist LLM judge wrapper (optional post-verify).
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import importlib.util

_spec = importlib.util.spec_from_file_location("bench_lib", REPO / "scripts" / "bench_lib.py")
bl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bl)


def score_checklist(task: dict, output: str, key: str) -> dict:
    checklist = task.get("checklist") or []
    if not checklist:
        return {"ratio": 0.0, "pass": False, "error": "no_checklist"}
    rubric = [c.get("item", str(c)) if isinstance(c, dict) else str(c) for c in checklist]
    task_copy = {**task, "rubric": rubric}
    verdict = bl.judge(task_copy, output, key)
    return verdict


if __name__ == "__main__":
    print("presentbench_checklist: import and call score_checklist() from bench runner")
