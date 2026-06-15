# Created: 2026-06-12
# Purpose: /api/onboarding/google 상태·해제 API (INT-1471) — 확정값 반환 계약 테스트
# Dependencies: web/routers/onboarding.py, pipeline/auth/google.py (mock — 실제 Keychain 접근 없음)
# Test Status: 전체 green 확인

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import pipeline.auth.google as g
from web.routers import onboarding


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(onboarding.router)
    return TestClient(app)


@pytest.fixture
def mock_google(monkeypatch):
    """pipeline.auth.google 을 결정적 상태로 고정 — 실제 Keychain 접근 차단."""
    state = {
        "accounts": [{"email": "a@x.com", "is_default": True},
                     {"email": "b@y.com", "is_default": False}],
        "authenticated": True,
        "email": "a@x.com",
    }
    monkeypatch.setattr(g, "is_configured", lambda: True)
    monkeypatch.setattr(g, "is_authenticated", lambda: state["authenticated"])
    monkeypatch.setattr(g, "stored_email", lambda: state["email"])
    monkeypatch.setattr(g, "stored_accounts", lambda: state["accounts"])
    return state


class TestGoogleStatus:
    def test_status_includes_accounts(self, client, mock_google):
        r = client.get("/api/onboarding/google")
        assert r.status_code == 200
        body = r.json()
        assert body["authenticated"] is True
        assert [a["email"] for a in body["accounts"]] == ["a@x.com", "b@y.com"]


class TestGoogleDisconnect:
    def test_disconnect_all_returns_confirmed_state(self, client, mock_google, monkeypatch):
        """낙관 응답이 아니라 삭제 후 실측 확정값(authenticated=False)을 반환해야 한다."""
        def fake_disconnect(account):
            assert account is None
            mock_google["authenticated"] = False
            mock_google["accounts"] = []
            mock_google["email"] = None
            return {"ok": True, "authenticated": False, "accounts": []}

        monkeypatch.setattr(g, "disconnect", fake_disconnect)
        r = client.post("/api/onboarding/google/disconnect", json={})
        assert r.status_code == 200
        body = r.json()
        assert body == {"ok": True, "authenticated": False, "accounts": [], "email": None}

    def test_disconnect_single_account(self, client, mock_google, monkeypatch):
        def fake_disconnect(account):
            assert account == "b@y.com"
            mock_google["accounts"] = [{"email": "a@x.com", "is_default": True}]
            return {"ok": True, "authenticated": True,
                    "accounts": mock_google["accounts"]}

        monkeypatch.setattr(g, "disconnect", fake_disconnect)
        r = client.post("/api/onboarding/google/disconnect", json={"account": "b@y.com"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["authenticated"] is True
        assert body["email"] == "a@x.com"
        assert [a["email"] for a in body["accounts"]] == ["a@x.com"]

    def test_disconnect_unknown_account_404(self, client, mock_google, monkeypatch):
        monkeypatch.setattr(g, "disconnect", lambda a: {
            "ok": False, "authenticated": True,
            "accounts": mock_google["accounts"],
            "error": f"연결되지 않은 계정: {a}"})
        r = client.post("/api/onboarding/google/disconnect", json={"account": "ghost@x.com"})
        assert r.status_code == 404
        assert r.json()["ok"] is False

    def test_disconnect_keychain_failure_500(self, client, mock_google, monkeypatch):
        monkeypatch.setattr(g, "disconnect", lambda a: {
            "ok": False, "authenticated": True,
            "accounts": mock_google["accounts"],
            "error": "Keychain 삭제 실패: b@y.com"})
        r = client.post("/api/onboarding/google/disconnect", json={"account": "b@y.com"})
        assert r.status_code == 500
        body = r.json()
        assert body["ok"] is False
        assert "삭제 실패" in body["error"]
        # 실패해도 확정 상태(authenticated/accounts)는 함께 반환 — 프론트가 그대로 표시
        assert body["authenticated"] is True

    def test_generic_disconnect_route_not_shadowing_google(self, client, mock_google, monkeypatch):
        """전용 google/disconnect 라우트가 동적 /{service}/disconnect 보다 먼저 매칭돼야 한다.

        동적 라우트가 먼저 매칭되면 payload 없는 logout() 경로로 빠져 계정별 해제가 불가능해진다.
        """
        called = {}

        def fake_disconnect(account):
            called["account"] = account
            return {"ok": True, "authenticated": True, "accounts": mock_google["accounts"]}

        monkeypatch.setattr(g, "disconnect", fake_disconnect)
        r = client.post("/api/onboarding/google/disconnect", json={"account": "b@y.com"})
        assert r.status_code == 200
        assert called.get("account") == "b@y.com"


class TestGoogleByo:
    """BYO OAuth 클라이언트 등록/우선순위 (2026-06-15)."""

    @pytest.fixture
    def kc(self, monkeypatch):
        """Keychain in-memory mock — 실제 save_byo_client/_load_client 경로를 탄다."""
        store: dict = {}
        monkeypatch.setattr(g._kc, "get_secret", lambda a, service=None: store.get((service, a)))
        monkeypatch.setattr(g._kc, "set_secret", lambda a, v, service=None: (store.__setitem__((service, a), v), True)[1])
        monkeypatch.setattr(g._kc, "delete_secret", lambda a, service=None: (store.pop((service, a), None), True)[1])
        # 내장 client 가 있다고 가정(폴백 검증용)
        monkeypatch.setattr(g, "_builtin_client", lambda: {
            "client_id": "BUILTIN.apps.googleusercontent.com", "client_secret": "bsec",
            "redirect_uri": g._DEFAULT_REDIRECT, "scopes": g._FALLBACK_SCOPES, "_source": "builtin"})
        return store

    def test_byo_save_desktop_json(self, client, kc):
        body = {"client_json": '{"installed":{"client_id":"99.apps.googleusercontent.com","client_secret":"GOCSPX-x"}}'}
        r = client.post("/api/onboarding/google/byo", json=body)
        assert r.status_code == 200 and r.json()["ok"] is True
        assert r.json()["client_source"] == "byo"
        assert r.json()["redirect_uri"] == g._DEFAULT_REDIRECT
        # 저장 후 _load_client 가 BYO 를 우선 반환
        assert g._load_client()["client_id"] == "99.apps.googleusercontent.com"

    def test_byo_save_id_secret_fields(self, client, kc):
        r = client.post("/api/onboarding/google/byo", json={
            "client_id": "12.apps.googleusercontent.com", "client_secret": "GOCSPX-y"})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert g.client_source() == "byo"

    def test_byo_rejects_web_client(self, client, kc):
        r = client.post("/api/onboarding/google/byo", json={
            "client_json": '{"web":{"client_id":"w.apps.googleusercontent.com","client_secret":"z"}}'})
        assert r.status_code == 400
        assert "데스크톱" in r.json()["error"]

    def test_byo_rejects_bad_client_id(self, client, kc):
        r = client.post("/api/onboarding/google/byo", json={
            "client_id": "not-a-google-id", "client_secret": "z"})
        assert r.status_code == 400

    def test_byo_rejects_invalid_json(self, client, kc):
        r = client.post("/api/onboarding/google/byo", json={"client_json": "{not json"})
        assert r.status_code == 400

    def test_byo_clear_falls_back_to_builtin(self, client, kc):
        client.post("/api/onboarding/google/byo", json={
            "client_id": "12.apps.googleusercontent.com", "client_secret": "GOCSPX-y"})
        assert g.client_source() == "byo"
        r = client.post("/api/onboarding/google/byo/clear", json={})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert r.json()["client_source"] == "builtin"
        assert g._load_client()["client_id"] == "BUILTIN.apps.googleusercontent.com"
