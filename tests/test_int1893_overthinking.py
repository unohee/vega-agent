# Created: 2026-06-24
# Purpose: INT-1893 overthinking — before/after 라운드 상한·멀티턴 분류 회귀.
# Dependencies: pipeline.tier_router, pipeline.streaming (mocked)

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from pipeline.tier_router import (
    legacy_load_from_user_blob,
    resolve_load_routing,
    route_load,
)

IKEA_MSG = "이케아 5만원 이하 사무용 조명 5개 추천해줘"
IKEA_MULTITURN = [
    {"role": "user", "content": "매출 데이터 분석해서 보고서 작성해줘"},
    {"role": "assistant", "content": "보고서 초안을 작성했습니다."},
    {"role": "user", "content": IKEA_MSG},
]


def test_int1893_before_after_ikea_multiturn():
    """Linear INT-1893 Done-when: 멀티턴+이케아 → max_rounds=10 (before 는 24)."""
    after = resolve_load_routing(IKEA_MULTITURN)
    before_load = legacy_load_from_user_blob(IKEA_MULTITURN)
    before_rounds = {"light": 10, "standard": 20, "heavy": 24}[before_load]
    assert before_load == "heavy"
    assert before_rounds == 24
    assert after["load"] == "light"
    assert after["max_rounds"] == 10


def test_int1893_before_after_short_analyze_prompt():
    """bare `분석해` regex 과매칭 before → light after."""
    prompt = "이 파일 분석해줘"
    assert route_load(prompt) == "light"
    assert resolve_load_routing([{"role": "user", "content": prompt}])["max_rounds"] == 10


def test_int1893_measurement_table_rows():
    """before/after 표 — scripts/measure_load_rounds.py 와 동기."""
    caps = {"light": 10, "standard": 20, "heavy": 24}
    scenarios = [
        ("ikea_multiturn", IKEA_MULTITURN),
        ("short_analyze", [{"role": "user", "content": "이 파일 분석해줘"}]),
    ]
    for name, msgs in scenarios:
        after = resolve_load_routing(msgs)
        before_load = legacy_load_from_user_blob(msgs)
        assert caps[before_load] >= after["max_rounds"] or name != "ikea_multiturn"
    ikea_before = legacy_load_from_user_blob(IKEA_MULTITURN)
    ikea_after = resolve_load_routing(IKEA_MULTITURN)
    assert caps[ikea_before] == 24
    assert ikea_after["max_rounds"] == 10


@pytest.mark.asyncio
async def test_stream_gpt_caps_max_rounds_for_ikea_with_heavy_history():
    """streaming path — stats.load=light, max_rounds=10 despite heavy history."""
    from pipeline import streaming

    async def on_token(_tok):
        pass

    def fake_stream_sse(req, token_q, tool_q, kind="chat_completions", stats_out=None, loop=None, reasoning_q=None):
        streaming._queue_put(token_q, "추천 목록입니다.", loop)
        streaming._queue_put(token_q, None, loop)
        streaming._queue_put(tool_q, None, loop)

    stats: dict = {}
    with patch("pipeline.streaming._build_request", return_value=(MagicMock(), "chat_completions")):
        with patch("pipeline.streaming._stream_sse", side_effect=fake_stream_sse):
            with patch("pipeline.streaming.build_dynamic_preamble", return_value=""):
                with patch("pipeline.model_catalog.resolve_turn_model", return_value=None):
                    result = await streaming.stream_gpt(
                        IKEA_MULTITURN,
                        "system",
                        on_token=on_token,
                        stats=stats,
                        tier="local",
                    )

    assert "추천" in result
    assert stats["load"] == "light"
    assert stats["max_rounds"] == 10
    assert stats["actual_rounds"] == 1
    assert stats["tool_rounds"] == 0
