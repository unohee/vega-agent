# Created: 2026-06-15
# Purpose: tier 라우팅 + set_active cloud-tier 동기화 회귀 (code-review xhigh, PR#7)
#   - set_active(sync_cloud_tier=True): cloud 계열이면 tiers.cloud 동기화, tiers=null 가드
#   - get_provider_for_tier('cloud'): 키 없는 bearer provider면 active로 read-time 폴백
# Dependencies: pipeline/llm_gateway.py
# Test Status: 신규

from __future__ import annotations

import importlib
import os
import tempfile

import pytest


@pytest.fixture
def gw(monkeypatch):
    """격리된 VEGA_DATA_DIR + 키 없는 환경으로 llm_gateway 재로드."""
    monkeypatch.setenv("VEGA_DATA_DIR", tempfile.mkdtemp())
    monkeypatch.delenv("OPENROUTER_API", raising=False)
    import pipeline.llm_gateway as g
    importlib.reload(g)
    # Keychain도 키 없음으로 — _has_usable_key가 env+Keychain 둘 다 봄
    import pipeline.keychain as kc
    monkeypatch.setattr(kc, "get_secret", lambda a, service=None: None)
    cfg = g._read_config()
    cfg["providers"] = {
        "chatgpt": {"auth_type": "chatgpt_oauth", "base_url": "https://chatgpt.com/x", "kind": "responses"},
        "openrouter": {"auth_type": "bearer", "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API"},
        "lmstudio": {"auth_type": "none", "base_url": "http://localhost:1234/v1"},
    }
    g._write_config(cfg)
    return g


class TestSetActiveSyncCloudTier:
    def test_sync_true_cloud_provider_updates_tiers(self, gw):
        gw.set_active("chatgpt", sync_cloud_tier=True)
        assert gw._read_config()["tiers"]["cloud"] == "chatgpt"

    def test_sync_false_leaves_tiers_untouched(self, gw):
        cfg = gw._read_config(); cfg["tiers"] = {"cloud": "openrouter"}; gw._write_config(cfg)
        gw.set_active("chatgpt")  # sync 안 함(기본 False)
        assert gw._read_config()["tiers"]["cloud"] == "openrouter"

    def test_sync_local_provider_excluded(self, gw):
        cfg = gw._read_config(); cfg["tiers"] = {"cloud": "chatgpt"}; gw._write_config(cfg)
        gw.set_active("lmstudio", sync_cloud_tier=True)  # local → cloud tier 안 건드림
        assert gw._read_config()["tiers"]["cloud"] == "chatgpt"

    def test_tiers_null_does_not_crash(self, gw):
        """수동 편집으로 tiers=null이어도 setdefault TypeError 안 남 (reader와 동일 방어)."""
        cfg = gw._read_config(); cfg["tiers"] = None; gw._write_config(cfg)
        gw.set_active("chatgpt", sync_cloud_tier=True)  # TypeError 나면 실패
        assert gw._read_config()["tiers"] == {"cloud": "chatgpt"}


class TestGetProviderForTierKeylessFallback:
    def test_cloud_keyless_falls_back_to_active(self, gw):
        """tiers.cloud=키없는 openrouter, active=chatgpt → active로 폴백."""
        cfg = gw._read_config(); cfg["active"] = "chatgpt"; cfg["tiers"] = {"cloud": "openrouter"}; gw._write_config(cfg)
        prov = gw.get_provider_for_tier("cloud")
        assert prov["name"] == "chatgpt"
        assert prov.get("_fell_back_from") == "cloud-keyless"

    def test_cloud_with_key_preserved(self, gw, monkeypatch):
        """키가 있으면 의도적 분리로 보고 그대로 둔다."""
        monkeypatch.setenv("OPENROUTER_API", "sk-or-test")
        cfg = gw._read_config(); cfg["active"] = "chatgpt"; cfg["tiers"] = {"cloud": "openrouter"}; gw._write_config(cfg)
        prov = gw.get_provider_for_tier("cloud")
        assert prov["name"] == "openrouter" and not prov.get("_fell_back_from")

    def test_active_is_keyless_cloud_no_infinite_fallback(self, gw):
        """active 자신이 키 없는 cloud면 폴백 안 하고 그대로(명시 에러 유도)."""
        cfg = gw._read_config(); cfg["active"] = "openrouter"; cfg["tiers"] = {"cloud": "openrouter"}; gw._write_config(cfg)
        prov = gw.get_provider_for_tier("cloud")
        assert prov["name"] == "openrouter" and not prov.get("_fell_back_from")

    def test_oauth_cloud_not_treated_keyless(self, gw):
        """OAuth(chatgpt) cloud tier는 키 개념이 달라 폴백 대상 아님 — 그대로."""
        cfg = gw._read_config(); cfg["active"] = "openrouter"; cfg["tiers"] = {"cloud": "chatgpt"}; gw._write_config(cfg)
        prov = gw.get_provider_for_tier("cloud")
        assert prov["name"] == "chatgpt"


class TestAutoRoute:
    """업무별 자동 라우터 vs 수동 선택 우선 (INT-1892)."""

    def test_select_concrete_model_disables_auto_route(self, gw):
        # 특정 모델 선택 → 그 모델 고정 + auto_route=False (수동 선택 우선)
        gw.update_model("openrouter", "deepseek/deepseek-v4-flash")
        prov = gw._read_config()["providers"]["openrouter"]
        assert prov["default_model"] == "deepseek/deepseek-v4-flash"
        assert prov["auto_route"] is False

    def test_select_auto_enables_route_keeps_default(self, gw):
        # "auto" 선택 → auto_route=True, default_model(폴백)은 유지
        gw.update_model("openrouter", "deepseek/deepseek-v4-flash")
        gw.update_model("openrouter", "auto")
        prov = gw._read_config()["providers"]["openrouter"]
        assert prov["auto_route"] is True
        assert prov["default_model"] == "deepseek/deepseek-v4-flash"  # 폴백 보존

    def test_list_providers_exposes_auto_route(self, gw):
        gw.update_model("openrouter", "auto")
        provs = {p["name"]: p for p in gw.list_providers()}
        assert provs["openrouter"]["auto_route"] is True

    def test_resolve_turn_model_none_when_auto_route_off(self, gw, monkeypatch):
        # auto_route=False면 라우터가 None → build_request가 default_model(수동 선택) 사용
        from pipeline import model_catalog
        cfg = gw._read_config(); cfg["active"] = "openrouter"
        cfg["providers"]["openrouter"]["auto_route"] = False
        gw._write_config(cfg)
        assert model_catalog.resolve_turn_model("standard") is None
