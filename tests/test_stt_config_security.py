# Created: 2026-07-01
# Purpose: STT config endpoint/api_key_env 화이트리스트 검증 (INT-2231 audit).
# Dependencies: pipeline/stt_gateway.py

from __future__ import annotations

import json

import pytest

from pipeline.stt_gateway import _endpoint_allowed, set_stt_config


def test_endpoint_allowed_wellknown():
    assert _endpoint_allowed("https://openrouter.ai/api/v1/audio/transcriptions")
    assert _endpoint_allowed("https://api.openai.com/v1/audio/transcriptions")
    assert _endpoint_allowed("https://api.groq.com/openai/v1/audio/transcriptions")


def test_endpoint_allowed_loopback():
    assert _endpoint_allowed("http://localhost:8765/v1/audio/transcriptions")
    assert _endpoint_allowed("http://127.0.0.1:1234/v1/audio/transcriptions")


def test_endpoint_rejects_external():
    # 임의 외부 endpoint 로 audio + secret 유출(SSRF/exfiltration) 차단
    assert not _endpoint_allowed("https://evil.example/x")
    assert not _endpoint_allowed("http://attacker.test/v1/audio/transcriptions")
    assert not _endpoint_allowed("https://openrouter.ai.attacker.test/x")


def test_set_config_rejects_bad_endpoint():
    with pytest.raises(ValueError):
        set_stt_config({"endpoint": "https://evil.example/x"})


def test_set_config_rejects_bad_key_env():
    with pytest.raises(ValueError):
        set_stt_config({"api_key_env": "AWS_SECRET_ACCESS_KEY"})


def test_set_config_allows_known(tmp_path, monkeypatch):
    import pipeline.stt_gateway as g
    p = tmp_path / "llm_providers.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(g, "_PROVIDERS_PATH", p)
    monkeypatch.setattr(g, "_REPO_PROVIDERS_PATH", tmp_path / "nonexist.json")
    g.set_stt_config({"provider": "openrouter", "api_key_env": "OPENROUTER_API"})
    assert json.loads(p.read_text(encoding="utf-8"))["stt"]["api_key_env"] == "OPENROUTER_API"


def test_set_config_allows_loopback_endpoint(tmp_path, monkeypatch):
    import pipeline.stt_gateway as g
    p = tmp_path / "llm_providers.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(g, "_PROVIDERS_PATH", p)
    monkeypatch.setattr(g, "_REPO_PROVIDERS_PATH", tmp_path / "nonexist.json")
    g.set_stt_config({"provider": "local", "endpoint": "http://localhost:8765/v1/audio/transcriptions"})
    assert "stt" in json.loads(p.read_text(encoding="utf-8"))
