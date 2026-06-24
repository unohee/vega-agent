# Created: 2026-06-24
# Purpose: L2 streaming load integration — payload + stats (INT-1893).

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

IKEA = [
    {"role": "user", "content": "매출 데이터 분석해서 보고서 작성해줘"},
    {"role": "assistant", "content": "..."},
    {"role": "user", "content": "이케아 5만원 이하 사무용 조명 5개 추천해줘"},
]


@pytest.mark.asyncio
async def test_stream_gpt_light_stats_and_budget_in_request():
    from pipeline import streaming

    captured: list[dict] = []

    def fake_build_request(input_items, system, **kwargs):
        captured.append(kwargs)
        req = MagicMock()
        req.data = json.dumps({"model": "m", "messages": [], "max_tokens": 1200}).encode()
        return req, "chat_completions"

    def fake_stream_sse(req, token_q, tool_q, kind="chat_completions", stats_out=None, loop=None, reasoning_q=None):
        streaming._queue_put(token_q, "답", loop)
        streaming._queue_put(token_q, None, loop)
        streaming._queue_put(tool_q, None, loop)

    stats: dict = {}
    async def on_token(_t: str) -> None:
        pass

    with patch("pipeline.streaming._build_request", side_effect=fake_build_request):
        with patch("pipeline.streaming._stream_sse", side_effect=fake_stream_sse):
            with patch("pipeline.streaming.build_dynamic_preamble", return_value=""):
                with patch("pipeline.model_catalog.resolve_turn_model", return_value=None):
                    await streaming.stream_gpt(IKEA, "sys", on_token=on_token, stats=stats, tier="local")

    assert stats["load"] == "light"
    assert stats["max_rounds"] == 10
    assert stats["max_tool_rounds"] == 2
    assert captured and captured[0].get("load") == "light"


@pytest.mark.asyncio
async def test_stream_gpt_load_override_heavy():
    from pipeline import streaming

    stats: dict = {}

    async def on_token(_t: str) -> None:
        pass

    def fake_stream_sse(req, token_q, tool_q, kind="chat_completions", stats_out=None, loop=None, reasoning_q=None):
        streaming._queue_put(token_q, "ok", loop)
        streaming._queue_put(token_q, None, loop)
        streaming._queue_put(tool_q, None, loop)

    short = [{"role": "user", "content": "이케아 조명 추천"}]
    with patch("pipeline.streaming._build_request", return_value=(MagicMock(), "chat_completions")):
        with patch("pipeline.streaming._stream_sse", side_effect=fake_stream_sse):
            with patch("pipeline.streaming.build_dynamic_preamble", return_value=""):
                with patch("pipeline.model_catalog.resolve_turn_model", return_value=None):
                    await streaming.stream_gpt(
                        short, "sys", on_token=on_token,
                        stats=stats, tier="local", load_override="heavy",
                    )
    assert stats["load"] == "heavy"
    assert stats["max_rounds"] == 24
