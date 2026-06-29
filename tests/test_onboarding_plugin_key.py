# Created: 2026-06-29
# Purpose: /api/onboarding/plugin-key — key 타입 워크스페이스 플러그인(Airtable/GitHub) PAT
#          GUI 연결 API (INT-1575). 검증→keychain 저장→toolset 게이트 자동 노출.
# Dependencies: web/routers/onboarding.py (mock — 실제 Keychain·네트워크 접근 없음)
# Test Status: passing

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routers import onboarding


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(onboarding.router)
    return TestClient(app)


@pytest.fixture
def fake_keychain(monkeypatch):
    """keychain set/delete 를 메모리 dict 로 — 실제 Keychain 차단."""
    store: dict[str, str] = {}
    import pipeline.keychain as kc
    monkeypatch.setattr(kc, "set_secret", lambda k, v, **kw: store.__setitem__(k, v) or True)
    monkeypatch.setattr(kc, "delete_secret", lambda k, **kw: store.pop(k, None))
    # invalidate 는 no-op 으로
    import pipeline.tool_registry as tr
    monkeypatch.setattr(tr, "invalidate_check_fn_cache", lambda: None)
    return store


def test_airtable_github_are_available_key_plugins():
    """4234/INT-1575 — airtable·github 가 coming_soon 이 아니라 연결 가능(available) key 플러그인."""
    by_id = {p["id"]: p for p in onboarding.PLUGIN_CATALOG}
    for pid in ("airtable", "github"):
        assert by_id[pid]["status"] == "available"
        assert by_id[pid]["auth"] == "key"
        assert by_id[pid]["key_env"]


def test_plugin_key_saves_after_verify(client, fake_keychain, monkeypatch):
    """검증 통과 시 PAT 를 keychain(key_env)에 저장하고 authenticated=True 반환."""
    monkeypatch.setattr(onboarding, "_verify_key", lambda entry, key: (True, ""))
    r = client.post("/api/onboarding/plugin-key",
                    json={"plugin": "airtable", "api_key": "patABC123"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "plugin": "airtable", "authenticated": True}
    assert fake_keychain.get("AIRTABLE_PERSONAL_ACCESS_TOKEN") == "patABC123"


def test_plugin_key_rejects_invalid(client, fake_keychain, monkeypatch):
    """검증 실패면 저장하지 않고 400."""
    monkeypatch.setattr(onboarding, "_verify_key",
                        lambda entry, key: (False, "키가 거부되었습니다 (HTTP 401)"))
    r = client.post("/api/onboarding/plugin-key",
                    json={"plugin": "airtable", "api_key": "bad"})
    assert r.status_code == 400
    assert "AIRTABLE_PERSONAL_ACCESS_TOKEN" not in fake_keychain


def test_plugin_key_empty_key_rejected(client, fake_keychain):
    r = client.post("/api/onboarding/plugin-key", json={"plugin": "github", "api_key": "  "})
    assert r.status_code == 400


def test_plugin_key_unknown_plugin_rejected(client, fake_keychain):
    r = client.post("/api/onboarding/plugin-key", json={"plugin": "nope", "api_key": "x"})
    assert r.status_code == 400


def test_plugin_key_rejects_non_key_plugin(client, fake_keychain):
    """google 은 oauth 타입 — plugin-key 로 저장 불가."""
    r = client.post("/api/onboarding/plugin-key", json={"plugin": "google", "api_key": "x"})
    assert r.status_code == 400


def test_plugin_key_delete_removes_secret(client, fake_keychain):
    fake_keychain["GITHUB_PERSONAL_ACCESS_TOKEN"] = "ghp_x"
    r = client.delete("/api/onboarding/plugin-key/github")
    assert r.status_code == 200 and r.json()["authenticated"] is False
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" not in fake_keychain
