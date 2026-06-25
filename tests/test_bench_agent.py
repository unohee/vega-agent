# Created: 2026-06-24
# Purpose: L2 agent bench harness mock tests (INT-1876).
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("bench_lib", _REPO / "scripts" / "bench_lib.py")
bl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bl)

_SPEC2 = importlib.util.spec_from_file_location("bench_agent", _REPO / "scripts" / "bench_agent.py")
ba = importlib.util.module_from_spec(_SPEC2)
_SPEC2.loader.exec_module(ba)


@pytest.mark.asyncio
async def test_run_agent_task_mock_streaming(tmp_path):
    task = {
        "id": "biz_email_reply",
        "category": "office",
        "prompt": "메일 회신",
        "rubric": ["톤", "내용"],
        "verify": "none",
    }

    async def fake_stream_gpt(*args, **kwargs):
        stats = kwargs.get("stats")
        if stats is not None:
            stats.update({"tool_rounds": 1, "actual_rounds": 2, "tokens_in": 100, "tokens_out": 50, "load": "standard"})
        return "정중한 회신 초안입니다."

    with patch("pipeline.streaming.stream_gpt", new=AsyncMock(side_effect=fake_stream_gpt)):
        with patch.object(ba.bl, "judge", return_value={"ratio": 1.0, "pass": True, "scores": []}):
            r = await ba.run_agent_task("test/model", task, "fake-key", sandbox_dir=tmp_path)

    assert r["harness"] == "agent"
    assert r["tool_rounds"] == 1
    assert r["pass"] is True


@pytest.mark.asyncio
async def test_run_agent_task_tool_gate_fail(tmp_path):
    task = {
        "id": "excel_create_e2e",
        "category": "office",
        "prompt": "",
        "agent_prompt": "xlsx 만들어",
        "rubric": ["파일"],
        "verify": "office",
        "required_tools": ["xlsx_create"],
        "min_tool_rounds": 1,
    }

    async def fake_stream_gpt(*args, **kwargs):
        stats = kwargs.get("stats")
        if stats is not None:
            stats.update({
                "tool_rounds": 0,
                "tools_called": [],
                "tokens_in": 10,
                "tokens_out": 5,
                "load": "standard",
            })
        return "완료"

    with patch("pipeline.streaming.stream_gpt", new=AsyncMock(side_effect=fake_stream_gpt)):
        with patch.object(ba.bl, "judge", return_value={"ratio": 1.0, "pass": True, "scores": []}):
            r = await ba.run_agent_task("test/model", task, "fake-key", sandbox_dir=tmp_path)

    assert r["tool_pass"] is False
    assert r["required_tools_met"] is False
    assert r["pass"] is False


def test_verify_office_excel_create_e2e(tmp_path):
    from pipeline.tools_office import xlsx_create

    fp = tmp_path / "monthly.xlsx"
    xlsx_create(str(fp), {
        "매출": [
            ["월", "매출"],
            ["1월", 120], ["2월", 95], ["3월", 140], ["4월", 110], ["5월", 130],
        ]
    })
    r = bl.verify_office({"id": "excel_create_e2e"}, "", sandbox_dir=tmp_path)
    assert r["exec_pass"] is True, r
