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


def test_build_request_light_sets_max_tokens_chat_completions():
    import pipeline.llm_gateway as gw

    fake_prov = {
        "name": "openrouter",
        "kind": "chat_completions",
        "auth_type": "bearer",
        "api_key_env": "OPENROUTER_API",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "test/model",
    }
    with patch.object(gw, "get_provider_for_tier", return_value=fake_prov), \
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
