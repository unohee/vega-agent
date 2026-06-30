# Created: 2026-06-24
# Purpose: LOAD_BUDGET + build_request(load) 회귀 (INT-1893 Phase 1).

from __future__ import annotations

import json
from unittest.mock import patch

from pipeline.tier_router import LOAD_BUDGET, resolve_load_routing


def test_load_budget_constants():
    assert LOAD_BUDGET["light"]["max_tokens"] == 1200
    assert LOAD_BUDGET["light"]["reasoning_effort"] == "low"


def test_resolve_load_routing_includes_budget():
    r = resolve_load_routing([{"role": "user", "content": "이케아 조명 추천"}])
    assert r["load"] == "light"
    assert r["budget"]["max_tokens"] == 1200
    assert r["max_tool_rounds"] == 2


_FAKE_OPENROUTER = {
    "name": "openrouter",
    "kind": "chat_completions",
    "auth_type": "bearer",
    "api_key_env": "OPENROUTER_API",
    "base_url": "https://openrouter.ai/api/v1",
    "default_model": "test/model",
}


def test_build_request_light_sets_max_tokens_chat_completions():
    import pipeline.llm_gateway as gw

    with patch.object(gw, "get_provider_for_tier", return_value=_FAKE_OPENROUTER), \
         patch.dict("os.environ", {"OPENROUTER_API": "sk-test"}):
        req, kind = gw.build_request(
            [{"role": "user", "content": "hi"}],
            "system",
            [],
            load="light",
        )
    payload = json.loads(req.data.decode())
    assert payload.get("max_tokens") == 1200
    assert kind == "chat_completions"


def test_build_request_all_loads_cap_max_tokens():
    # 모든 load 에 max_tokens 상한을 둬 무한 spew 를 막는다 (INT-1999 (d)).
    import pipeline.llm_gateway as gw

    with patch.object(gw, "get_provider_for_tier", return_value=_FAKE_OPENROUTER), \
         patch.dict("os.environ", {"OPENROUTER_API": "sk-test"}):
        for load, expected in (("light", 1200), ("standard", 4000), ("heavy", 8000)):
            req, kind = gw.build_request(
                [{"role": "user", "content": "hi"}], "system", [], load=load
            )
            assert kind == "chat_completions"
            payload = json.loads(req.data.decode())
            assert payload.get("max_tokens") == expected, f"{load} → {payload.get('max_tokens')}"


def test_build_request_adds_degeneration_penalties():
    # repetition/frequency penalty 로 garbage spew 를 억제한다 (INT-1999 (a)).
    # temperature 는 건드리지 않는다(창의 작업 보호).
    import pipeline.llm_gateway as gw

    with patch.object(gw, "get_provider_for_tier", return_value=_FAKE_OPENROUTER), \
         patch.dict("os.environ", {"OPENROUTER_API": "sk-test"}):
        req, kind = gw.build_request(
            [{"role": "user", "content": "hi"}], "system", [], load="standard"
        )
    payload = json.loads(req.data.decode())
    assert payload.get("frequency_penalty") == 0.3
    assert payload.get("repetition_penalty") == 1.1
    assert "temperature" not in payload  # provider 기본값 유지
