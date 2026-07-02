# Created: 2026-07-02
# Purpose: STT stale-config 폴백 가드 — v0.1.50 이전 openai 설정 잔존 + 키 없음이면
#          OpenRouter default 로 폴백해 "OpenAI 401 오류"를 방지 (STT 중단 시 사고).

from __future__ import annotations

import pipeline.stt_gateway as g


def test_stale_openai_no_key_falls_back_to_openrouter(monkeypatch):
    """저장 provider=openai 인데 그 키가 없고 OpenRouter 키는 있으면 openrouter default 로."""
    monkeypatch.setattr(g, "_read_config", lambda: {"stt": {"provider": "openai", "model": "whisper-1"}})
    monkeypatch.setattr(g, "_resolve_api_key", lambda cfg: "")     # openai 키 없음
    monkeypatch.setattr(g, "_has_openrouter_key", lambda: True)    # openrouter 키 있음
    cfg = g.get_stt_config()
    assert cfg["provider"] == "openrouter"
    assert cfg["model"] == g._DEFAULT_STT["model"]


def test_openai_with_valid_key_preserved(monkeypatch):
    """openai 키가 있으면 그 설정을 그대로 유지(폴백 안 함)."""
    monkeypatch.setattr(g, "_read_config", lambda: {"stt": {"provider": "openai", "model": "whisper-1"}})
    monkeypatch.setattr(g, "_resolve_api_key", lambda cfg: "sk-openai")
    monkeypatch.setattr(g, "_has_openrouter_key", lambda: True)
    assert g.get_stt_config()["provider"] == "openai"


def test_no_openrouter_key_keeps_stale_config(monkeypatch):
    """OpenRouter 키도 없으면 폴백해도 무의미 — 저장값 유지(사용자가 키 설정하도록)."""
    monkeypatch.setattr(g, "_read_config", lambda: {"stt": {"provider": "openai"}})
    monkeypatch.setattr(g, "_resolve_api_key", lambda cfg: "")
    monkeypatch.setattr(g, "_has_openrouter_key", lambda: False)
    assert g.get_stt_config()["provider"] == "openai"


def test_local_provider_preserved(monkeypatch):
    """로컬 provider 는 키 불필요 — 폴백 대상 아님."""
    monkeypatch.setattr(g, "_read_config", lambda: {"stt": {"provider": "local"}})
    monkeypatch.setattr(g, "_resolve_api_key", lambda cfg: "")
    monkeypatch.setattr(g, "_has_openrouter_key", lambda: True)
    assert g.get_stt_config()["provider"] == "local"


def test_openrouter_no_key_not_touched(monkeypatch):
    """이미 openrouter 면 폴백 대상 아님(키 없어도 그대로 — 자기 자신으로 폴백 무의미)."""
    monkeypatch.setattr(g, "_read_config", lambda: {"stt": {"provider": "openrouter"}})
    monkeypatch.setattr(g, "_resolve_api_key", lambda cfg: "")
    monkeypatch.setattr(g, "_has_openrouter_key", lambda: True)
    assert g.get_stt_config()["provider"] == "openrouter"


def test_no_stt_section_uses_default(monkeypatch):
    """stt 설정이 없으면 _DEFAULT_STT(openrouter)."""
    monkeypatch.setattr(g, "_read_config", lambda: {})
    assert g.get_stt_config()["provider"] == "openrouter"
