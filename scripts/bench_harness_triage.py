#!/usr/bin/env python3
# Created: 2026-06-25
# Purpose: Offline harness triage for INT-1920–1923 (no API).
"""Replay saved bench rows / ingest samples to sanity-check harness gates."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_SPEC = importlib.util.spec_from_file_location("bench_lib", REPO / "scripts" / "bench_lib.py")
bl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bl)

_SPEC_I = importlib.util.spec_from_file_location("ingest", REPO / "scripts" / "bench_external" / "ingest.py")
ingest = importlib.util.module_from_spec(_SPEC_I)
_SPEC_I.loader.exec_module(ingest)


def _report(name: str, data: dict) -> None:
    print(json.dumps({"check": name, **data}, ensure_ascii=False))


def triage_mbpp_ingest() -> None:
    line = "assert set(similar_elements((3, 4, 5, 6),(5, 7, 4, 10))) == set((4, 5))"
    fixed = ingest._mbpp_assert_line(line)
    syntax_ok = True
    try:
        compile(fixed, "<mbpp>", "exec")
    except SyntaxError as e:
        syntax_ok = False
        _report("mbpp_syntax", {"ok": False, "error": str(e)})
    _report("mbpp_ingest_line", {"syntax_ok": syntax_ok, "line": fixed[:80]})


def triage_swebench_verify_first() -> None:
    task = {"id": "ext_swebench_lite_004", "source": "swebench_lite", "category": "swe"}
    vf = bl.task_is_verify_first(task)
    mp = bl.merge_pass(task, judge_pass=False, verify={"exec_pass": True}, subjective=True)
    _report("swebench_verify_first", {"verify_first": vf, "merge_pass_despite_judge_fail": mp})


def triage_tool_alias() -> None:
    task = {"id": "excel_read_fix_save", "category": "office", "required_tools": ["xlsx_read", "xlsx_create"]}
    stats = {"tools_called": ["file_read", "xlsx_create"], "tool_rounds": 2}
    tv = bl.verify_tool_use(task, stats)
    _report("tool_alias", {"tool_pass": tv["tool_pass"], "missing_tools": tv["missing_tools"]})


def triage_odyssey_verify_label() -> None:
    tasks = ingest.ingest_odysseybench(limit=3)
    _report("odyssey_verify_field", {t["id"]: t.get("verify") for t in tasks})


def triage_replay_toolcalling_artifact() -> None:
    path = REPO / "build_output" / "bench_toolcalling_agent.json"
    if not path.is_file():
        _report("toolcalling_replay", {"skipped": True, "reason": "artifact missing"})
        return
    rows = json.loads(path.read_text())["results"]
    replay = flipped = 0
    for r in rows:
        if r.get("task") != "excel_read_fix_save" or r.get("pass"):
            continue
        tv = r.get("tool_verify") or {}
        if tv.get("missing_tools") != ["xlsx_read"]:
            continue
        if "file_read" not in (r.get("tools_called") or []):
            continue
        replay += 1
        new_tv = bl.verify_tool_use(
            {"id": r["task"], "category": "office", "required_tools": ["xlsx_read", "xlsx_create"]},
            tool_trace=r.get("tools_called") or [],
        )
        if new_tv.get("tool_pass") and not r.get("pass"):
            flipped += 1
    _report("toolcalling_replay", {"candidates": replay, "would_flip_tool_pass": flipped})


def main() -> int:
    triage_mbpp_ingest()
    triage_swebench_verify_first()
    triage_tool_alias()
    triage_odyssey_verify_label()
    triage_replay_toolcalling_artifact()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
