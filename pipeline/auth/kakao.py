# Created: 2026-07-02
# Purpose: Kakao OAuth — "send to me" (talk_message) user consent flow (INT-2322).
#   Mirrors pipeline/auth/slack.py structurally: backend owns the redirect route
#   GET /kakao/callback (http://127.0.0.1:8100/kakao/callback), tokens live only  # cxt-ignore: fake_data
#   in the secure store (pipeline/keychain.py). The Kakao REST API key is read
#   exclusively from Keychain account KAKAO_REST_API_KEY (service "VEGA") — never
#   hardcoded, never from .env (get_secret has no .env/env fallback chain).
# Dependencies: stdlib only (urllib + json), pipeline.keychain
# Test Status: tests/test_kakao_int2322.py

from __future__ import annotations

import json
import secrets
import ssl
import time
import urllib.parse
import urllib.request

from pipeline import keychain as _kc

KEYCHAIN_SERVICE = "vega-kakao-oauth"
_AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
# Kakao console requires the exact redirect URI to be registered. Same local
# callback shape as slack/superthread (backend-owned route).
_DEFAULT_REDIRECT = "http://127.0.0.1:8100/kakao/callback"  # cxt-ignore: fake_data
_SCOPES = "talk_message"

# REST API key lives in the default "VEGA" keychain service (user pastes it once).
_REST_KEY_ACCOUNT = "KAKAO_REST_API_KEY"

_pending_state: dict[str, str] = {}


class KakaoOAuthNotConfigured(RuntimeError):
    """Keychain has no KAKAO_REST_API_KEY."""


def _rest_key() -> str | None:
    """Kakao REST API key — Keychain only (no .env/env fallback by design)."""
    return (_kc.get_secret(_REST_KEY_ACCOUNT) or "").strip() or None


def is_configured() -> bool:
    return bool(_rest_key())


# Token storage delegates to the cross-platform secure store (pipeline/keychain.py,
# INT-1494) — macOS=Keychain, Windows=Credential Manager. Same helpers as slack.py.
def keychain_save(account: str, value: str) -> None:
    if not _kc.set_secret(account, value, service=KEYCHAIN_SERVICE):
        raise RuntimeError(f"토큰 저장 실패(secure store 미가용): {account}")


def keychain_load(account: str) -> str | None:
    return _kc.get_secret(account, service=KEYCHAIN_SERVICE)


def keychain_delete(account: str) -> None:
    _kc.delete_secret(account, service=KEYCHAIN_SERVICE)


def is_authenticated() -> bool:
    """access 또는 refresh 토큰 보유 여부 — tool_registry 가용성 게이트가 호출."""
    return bool(keychain_load("kakao_access") or keychain_load("kakao_refresh"))


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _token_request(payload: dict[str, str]) -> dict:
    """POST kauth.kakao.com/oauth/token (form-urlencoded) — 교환·갱신 공용."""
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        _TOKEN_URL, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
        return json.loads(r.read().decode())


def authorize_url() -> str:
    """브라우저로 열 카카오 동의 URL. 백엔드 GET /kakao/auth 가 302로 보낸다."""
    key = _rest_key()
    if not key:
        raise KakaoOAuthNotConfigured(
            "KAKAO_REST_API_KEY 가 Keychain(service=VEGA)에 없습니다. "
            "카카오 개발자 콘솔의 REST API 키를 먼저 저장하세요."
        )
    state = secrets.token_urlsafe(16)
    _pending_state["state"] = state
    params = urllib.parse.urlencode({
        "client_id": key,
        "redirect_uri": _DEFAULT_REDIRECT,
        "response_type": "code",
        "scope": _SCOPES,
        "state": state,
    })
    return f"{_AUTHORIZE_URL}?{params}"


def _save_tokens(resp: dict) -> None:
    """토큰 응답 저장. refresh_token 은 응답에 **있을 때만** 교체 —
    카카오는 잔여 유효기간 1개월 미만일 때만 갱신 응답에 동봉한다."""
    keychain_save("kakao_access", resp["access_token"])
    if resp.get("refresh_token"):
        keychain_save("kakao_refresh", resp["refresh_token"])
    if resp.get("expires_in"):
        keychain_save("kakao_expires_at", str(int(time.time()) + int(resp["expires_in"])))


def exchange_code(code: str, state: str | None = None) -> dict:
    """code → 토큰 교환 → Keychain 저장. 백엔드 GET /kakao/callback 가 호출.
    반환: {"ok", "error"}."""
    # Exact state match required, fail-closed (INT-2233 — same as google/slack):
    # missing pending or mismatch would otherwise allow login CSRF / session injection.
    if not _pending_state.get("state") or state != _pending_state["state"]:
        return {"ok": False, "error": "state 불일치 — 보안상 중단"}
    key = _rest_key()
    if not key:
        return {"ok": False, "error": "KAKAO_REST_API_KEY 가 Keychain 에 없습니다."}
    try:
        resp = _token_request({
            "grant_type": "authorization_code",
            "client_id": key,
            "redirect_uri": _DEFAULT_REDIRECT,
            "code": code,
        })
    except Exception as e:
        return {"ok": False, "error": f"토큰 교환 실패: {e}"}
    if not resp.get("access_token"):
        detail = resp.get("error_description") or resp.get("error") or "access_token 미수신"
        return {"ok": False, "error": f"Kakao: {detail}"}
    _save_tokens(resp)
    _pending_state.pop("state", None)
    return {"ok": True, "error": None}


def refresh_access_token() -> str | None:
    """refresh_token 으로 access token 갱신. 실패/미보유 시 None."""
    refresh = keychain_load("kakao_refresh")
    key = _rest_key()
    if not refresh or not key:
        return None
    try:
        resp = _token_request({
            "grant_type": "refresh_token",
            "client_id": key,
            "refresh_token": refresh,
        })
    except Exception:
        return None
    if not resp.get("access_token"):
        return None
    _save_tokens(resp)
    return resp["access_token"]


def access_token() -> str | None:
    """유효한 access token. 만료 임박(5분 마진) 시 refresh 자동 갱신. 없으면 None."""
    token = keychain_load("kakao_access")
    if token:
        exp = keychain_load("kakao_expires_at")
        try:
            if not exp or time.time() < int(exp) - 300:
                return token
        except ValueError:
            return token
    return refresh_access_token()


def logout() -> None:
    """토큰 삭제. airtable/github 의 _LOGOUT_FLAG tombstone(INT-2233)은 _kc.get 의
    .env/환경변수 폴백을 무력화하기 위한 장치인데, 카카오 토큰은 이 서비스 전용
    Keychain 슬롯에만 존재하고 폴백 경로가 없다 — 단순 삭제로 충분해 tombstone 생략."""
    for account in ("kakao_access", "kakao_refresh", "kakao_expires_at"):
        keychain_delete(account)
