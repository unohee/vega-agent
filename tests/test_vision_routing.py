# Created: 2026-06-11
# Purpose: 이미지 포함 턴의 비전 모델 라우팅 회귀 테스트 (INT-1466)
#          — OpenRouter 기본 모델(deepseek)이 이미지 미지원이라 404 나던 버그.
# Dependencies: pytest, unittest.mock
# Test Status: passing

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import llm_gateway as lg

_OR_PROV = {
    "name": "openrouter",
    "label": "OpenRouter",
    "kind": "chat_completions",
    "auth_type": "bearer",
    "api_key_env": "OPENROUTER_API",
    "base_url": "https://openrouter.ai/api/v1",
    "default_model": "deepseek/deepseek-v4-flash",
}

_TEXT_ITEMS = [{"role": "user", "content": "안녕"}]
_IMAGE_ITEMS = [{"role": "user", "content": [
    {"type": "input_text", "text": "이거 읽어줘"},
    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
]}]


def _payload(req):
    return json.loads(req.data.decode())


def test_image_turn_switches_to_vision_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API", "sk-test")
    with patch.object(lg, "get_active_provider", return_value=dict(_OR_PROV)):
        req, kind = lg.build_request(_IMAGE_ITEMS, "sys", [])
    assert kind == "chat_completions"
    assert _payload(req)["model"] == "google/gemini-3.1-flash-lite"


def test_text_turn_keeps_default_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API", "sk-test")
    with patch.object(lg, "get_active_provider", return_value=dict(_OR_PROV)):
        req, _ = lg.build_request(_TEXT_ITEMS, "sys", [])
    assert _payload(req)["model"] == "deepseek/deepseek-v4-flash"


def test_explicit_vision_model_overrides_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API", "sk-test")
    prov = dict(_OR_PROV, vision_model="openai/gpt-5.5")
    with patch.object(lg, "get_active_provider", return_value=prov):
        req, _ = lg.build_request(_IMAGE_ITEMS, "sys", [])
    assert _payload(req)["model"] == "openai/gpt-5.5"


def test_provider_without_vision_mapping_untouched(monkeypatch):
    """비전 매핑이 없는 프로바이더(로컬 등)는 모델을 바꾸지 않는다."""
    prov = {"name": "lmstudio", "kind": "chat_completions", "auth_type": "none",
            "base_url": "http://localhost:1234/v1", "default_model": "gemma-4-26b"}
    with patch.object(lg, "get_active_provider", return_value=prov):
        req, _ = lg.build_request(_IMAGE_ITEMS, "sys", [])
    assert _payload(req)["model"] == "gemma-4-26b"


def test_image_payload_converted_to_chat_format(monkeypatch):
    """input_image 블록이 ChatCompletions image_url 형식으로 변환되는지."""
    monkeypatch.setenv("OPENROUTER_API", "sk-test")
    with patch.object(lg, "get_active_provider", return_value=dict(_OR_PROV)):
        req, _ = lg.build_request(_IMAGE_ITEMS, "sys", [])
    msgs = _payload(req)["messages"]
    user = next(m for m in msgs if m["role"] == "user")
    img_blocks = [c for c in user["content"] if c.get("type") == "image_url"]
    assert img_blocks and img_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")
