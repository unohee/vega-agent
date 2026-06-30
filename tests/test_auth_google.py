# Created: 2026-06-12
# Purpose: pipeline/auth/google.py — keychain_delete returncode 검증·멀티계정·해제 확정값 (INT-1471)
# Dependencies: pipeline/auth/google.py (Keychain은 전부 mock — 실제 macOS Keychain 변이 금지)
# Test Status: 전체 green 확인

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pipeline.auth.google import (
    KEYCHAIN_ACCOUNT,
    _ACCOUNTS_INDEX_SLOT,
    _FALLBACK_SCOPES,
    _token_slot,
    disconnect,
    exchange_code,
    get_access_token,
    is_authenticated,
    keychain_delete,
    logout,
    stored_accounts,
)


class FakeKeychain:
    """in-memory Keychain — load/save/delete 를 통째로 대체해 subprocess 격리."""

    def __init__(self, initial: dict | None = None):
        self.store = dict(initial or {})

    def load(self, account):
        return self.store.get(account)

    def save(self, account, value):
        self.store[account] = value

    def delete(self, account):
        self.store.pop(account, None)
        return True


@pytest.fixture
def fake_kc(monkeypatch):
    kc = FakeKeychain()
    monkeypatch.setattr("pipeline.auth.google.keychain_load", kc.load)
    monkeypatch.setattr("pipeline.auth.google.keychain_save", kc.save)
    monkeypatch.setattr("pipeline.auth.google.keychain_delete", kc.delete)
    return kc


_FAKE_CLIENT = {
    "client_id": "cid", "client_secret": "sec",
    "redirect_uri": "http://localhost:8100/google/callback",
    "scopes": _FALLBACK_SCOPES,
}


class TestKeychainDelete:
    """keychain_delete returncode 검증 (INT-1471). 토큰 저장은 크로스플랫폼 중앙
    keychain(_security)에 위임되므로(INT-1494), macOS 경로의 `_security` 를 mock 한다.
    멱등(없는 항목=True)·실제 실패(권한 등=False) 계약은 위임 후에도 보존돼야 한다."""

    def test_keychain_delete_success(self):
        with patch("pipeline.keychain._HAS_KEYCHAIN", True), \
             patch("pipeline.keychain._security", return_value=(0, "")):
            assert keychain_delete("refresh_token") is True

    def test_keychain_delete_not_found_is_idempotent(self):
        """항목이 원래 없으면(44 / could not be found) 멱등 성공 (INT-1471)."""
        with patch("pipeline.keychain._HAS_KEYCHAIN", True), \
             patch("pipeline.keychain._security", return_value=(44, "")):
            assert keychain_delete("refresh_token") is True
        with patch("pipeline.keychain._HAS_KEYCHAIN", True), \
             patch("pipeline.keychain._security",
                   return_value=(1, "The specified item could not be found in the keychain.")):
            assert keychain_delete("refresh_token") is True

    def test_keychain_delete_real_failure_returns_false(self):
        """returncode 비0 + not-found 가 아닌 에러는 실패로 보고해야 한다 (INT-1471 회귀)."""
        with patch("pipeline.keychain._HAS_KEYCHAIN", True), \
             patch("pipeline.keychain._security",
                   return_value=(51, "User interaction is not allowed.")):
            assert keychain_delete("refresh_token") is False


class TestMultiAccount:
    """INT-1471 — Keychain 멀티슬롯(refresh_token::<email>) + accounts 인덱스."""

    def test_stored_accounts_empty(self, fake_kc):
        assert stored_accounts() == []

    def test_stored_accounts_from_index(self, fake_kc):
        fake_kc.save(_ACCOUNTS_INDEX_SLOT, json.dumps(["a@x.com", "b@y.com"]))
        fake_kc.save(_token_slot("a@x.com"), "tok_a")
        fake_kc.save(_token_slot("b@y.com"), "tok_b")
        accs = stored_accounts()
        assert [a["email"] for a in accs] == ["a@x.com", "b@y.com"]
        assert accs[0]["is_default"] is True
        assert accs[1]["is_default"] is False

    def test_legacy_single_slot_migrates_to_default_account(self, fake_kc):
        """기존 단일 슬롯(refresh_token) 사용자는 기본 계정으로 자동 인식·마이그레이션."""
        fake_kc.save(KEYCHAIN_ACCOUNT, "legacy_tok")
        fake_kc.save("email", "old@x.com")
        accs = stored_accounts()
        assert accs == [{"email": "old@x.com", "is_default": True}]
        assert fake_kc.load(_token_slot("old@x.com")) == "legacy_tok"
        assert json.loads(fake_kc.load(_ACCOUNTS_INDEX_SLOT)) == ["old@x.com"]

    def test_account_arg_backward_compat_no_accounts(self, fake_kc):
        """구버전 get_access_token('personal') 호출 — 연결 계정이 없으면 None (하위호환)."""
        assert get_access_token("personal") is None

    def test_get_access_token_resolves_account_email(self, fake_kc):
        fake_kc.save(_ACCOUNTS_INDEX_SLOT, json.dumps(["a@x.com", "b@y.com"]))
        fake_kc.save(KEYCHAIN_ACCOUNT, "tok_a")
        fake_kc.save(_token_slot("a@x.com"), "tok_a")
        fake_kc.save(_token_slot("b@y.com"), "tok_b")
        with patch("pipeline.auth.google._load_client", return_value=_FAKE_CLIENT), \
             patch("pipeline.auth.google.refresh_access_token",
                   side_effect=lambda rt, c, s: "access_" + rt):
            assert get_access_token("b@y.com") == "access_tok_b"
            assert get_access_token("a@x.com") == "access_tok_a"
            # 미일치 식별자 → strict fail-closed: 기본 계정으로 폴백하지 않음 (INT-2233)
            assert get_access_token("nobody@nowhere") is None

    def test_get_access_token_resolves_profile_key(self, fake_kc):
        """user_profile email_accounts 의 key('personal' 등)로도 계정을 찾는다."""
        fake_kc.save(_ACCOUNTS_INDEX_SLOT, json.dumps(["a@x.com", "b@y.com"]))
        fake_kc.save(KEYCHAIN_ACCOUNT, "tok_a")
        fake_kc.save(_token_slot("a@x.com"), "tok_a")
        fake_kc.save(_token_slot("b@y.com"), "tok_b")
        with patch("pipeline.user_profile.email_accounts", return_value=[
                {"key": "work", "email": "b@y.com", "label": "work"}]), \
             patch("pipeline.auth.google._load_client", return_value=_FAKE_CLIENT), \
             patch("pipeline.auth.google.refresh_access_token",
                   side_effect=lambda rt, c, s: "access_" + rt):
            assert get_access_token("work") == "access_tok_b"

    def _userinfo_resp(self, email: str):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"email": email}).encode()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_exchange_code_first_account_writes_slot_and_mirror(self, fake_kc):
        """첫 계정 — 계정별 슬롯 + 레거시 미러 둘 다 기록 (INT-1471)."""
        token_resp = {"access_token": "at", "refresh_token": "tok_first"}
        import pipeline.auth.google as _g
        _g._pending_state["state"] = "s1"  # state 검증 강화 (INT-2233): 유효 state 세팅
        with patch("pipeline.auth.google._load_client", return_value=_FAKE_CLIENT), \
             patch("pipeline.auth.google._token_request", return_value=token_resp), \
             patch("pipeline.auth.google.urllib.request.urlopen",
                   return_value=self._userinfo_resp("user@example.com")):
            result = exchange_code(code="4/0AY0e-gAbc...", state="s1")
        assert result["ok"] is True
        assert result["email"] == "user@example.com"
        assert fake_kc.load(_token_slot("user@example.com")) == "tok_first"
        assert fake_kc.load(KEYCHAIN_ACCOUNT) == "tok_first"
        assert json.loads(fake_kc.load(_ACCOUNTS_INDEX_SLOT)) == ["user@example.com"]

    def test_exchange_code_second_account_keeps_default_mirror(self, fake_kc):
        """두 번째 계정 연결이 기본 계정의 레거시 미러를 덮어쓰지 않아야 한다."""
        fake_kc.save(_ACCOUNTS_INDEX_SLOT, json.dumps(["a@x.com"]))
        fake_kc.save(KEYCHAIN_ACCOUNT, "tok_a")
        fake_kc.save("email", "a@x.com")
        fake_kc.save(_token_slot("a@x.com"), "tok_a")
        token_resp = {"access_token": "at", "refresh_token": "tok_b"}
        import pipeline.auth.google as _g
        _g._pending_state["state"] = "s1"  # state 검증 강화 (INT-2233): 유효 state 세팅
        with patch("pipeline.auth.google._load_client", return_value=_FAKE_CLIENT), \
             patch("pipeline.auth.google._token_request", return_value=token_resp), \
             patch("pipeline.auth.google.urllib.request.urlopen",
                   return_value=self._userinfo_resp("b@y.com")):
            result = exchange_code(code="code", state="s1")
        assert result["ok"] is True
        assert json.loads(fake_kc.load(_ACCOUNTS_INDEX_SLOT)) == ["a@x.com", "b@y.com"]
        assert fake_kc.load(_token_slot("b@y.com")) == "tok_b"
        assert fake_kc.load(KEYCHAIN_ACCOUNT) == "tok_a"   # 기본 미러 보존
        assert fake_kc.load("email") == "a@x.com"


class TestDisconnect:
    """INT-1471 — 해제 후 실측 재조회 확정값 반환."""

    def _two_accounts(self, fake_kc):
        fake_kc.save(_ACCOUNTS_INDEX_SLOT, json.dumps(["a@x.com", "b@y.com"]))
        fake_kc.save(KEYCHAIN_ACCOUNT, "tok_a")
        fake_kc.save("email", "a@x.com")
        fake_kc.save(_token_slot("a@x.com"), "tok_a")
        fake_kc.save(_token_slot("b@y.com"), "tok_b")

    def test_disconnect_all(self, fake_kc):
        self._two_accounts(fake_kc)
        result = disconnect(None)
        assert result["ok"] is True
        assert result["authenticated"] is False
        assert result["accounts"] == []
        assert fake_kc.load(KEYCHAIN_ACCOUNT) is None
        assert fake_kc.load(_token_slot("a@x.com")) is None
        assert fake_kc.load(_token_slot("b@y.com")) is None

    def test_disconnect_non_default_account(self, fake_kc):
        self._two_accounts(fake_kc)
        result = disconnect("b@y.com")
        assert result["ok"] is True
        assert result["authenticated"] is True
        assert [a["email"] for a in result["accounts"]] == ["a@x.com"]
        assert fake_kc.load(KEYCHAIN_ACCOUNT) == "tok_a"   # 기본 계정 영향 없음
        assert fake_kc.load(_token_slot("b@y.com")) is None

    def test_disconnect_default_promotes_next(self, fake_kc):
        self._two_accounts(fake_kc)
        result = disconnect("a@x.com")
        assert result["ok"] is True
        assert result["authenticated"] is True
        assert [a["email"] for a in result["accounts"]] == ["b@y.com"]
        # 다음 계정 승격 — 레거시 미러·email 동기화
        assert fake_kc.load(KEYCHAIN_ACCOUNT) == "tok_b"
        assert fake_kc.load("email") == "b@y.com"

    def test_disconnect_unknown_account(self, fake_kc):
        self._two_accounts(fake_kc)
        result = disconnect("ghost@x.com")
        assert result["ok"] is False
        assert "연결되지 않은 계정" in result["error"]
        assert result["authenticated"] is True   # 기존 연결은 그대로

    def test_disconnect_reports_keychain_failure(self, fake_kc, monkeypatch):
        """삭제가 실패하면(returncode 검증) ok=False + 실패 슬롯을 보고해야 한다."""
        self._two_accounts(fake_kc)
        monkeypatch.setattr("pipeline.auth.google.keychain_delete", lambda a: False)
        result = disconnect("b@y.com")
        assert result["ok"] is False
        assert "삭제 실패" in result["error"]

    def test_logout_clears_everything(self, fake_kc):
        self._two_accounts(fake_kc)
        logout()
        assert is_authenticated() is False
        assert stored_accounts() == []
