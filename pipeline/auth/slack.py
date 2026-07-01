# Created: 2026-06-01
# Purpose: Slack OAuth v2 (user token xoxp) — 사원이 브라우저 동의로 본인 계정 연결.
#   Google(pipeline/auth/google.py) 패턴을 Slack 에 맞게. redirect 는 백엔드
#   라우트 GET /slack/callback (http://localhost:8100/slack/callback) 로 들어온다.  # cxt-ignore: fake_data
# Dependencies: stdlib only (urllib + json + subprocess for keychain)

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import subprocess
import time
import urllib.parse
import urllib.request

from pipeline import keychain as _kc
from pipeline.data_paths import slack_oauth_client_path

KEYCHAIN_SERVICE = "vega-slack-oauth"
_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
_TOKEN_URL = "https://slack.com/api/oauth.v2.access"

_pending_state: dict[str, str] = {}


def _pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) 생성. S256 방식."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


class SlackOAuthNotConfigured(RuntimeError):
    """data/slack_oauth_client.json 이 없을 때."""


def _load_client() -> dict:
    path = slack_oauth_client_path()
    if not path:
        raise SlackOAuthNotConfigured(
            "Slack OAuth 내장 클라이언트(data/slack_oauth_client.json)가 없습니다. "
            "이 빌드는 Slack 사용자 연결을 지원하지 않습니다."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    cid = (data.get("client_id") or "").strip()
    csec = (data.get("client_secret") or "").strip()
    if not cid or not csec:
        raise SlackOAuthNotConfigured("slack_oauth_client.json 에 client_id/secret 이 비어 있습니다.")
    _FALLBACK_USER_SCOPES = [
        "channels:read", "channels:history", "groups:history",
        "im:history", "mpim:history", "users:read", "search:read",
        "chat:write",  # slack_send_message — INT-1882
    ]
    stored = data.get("user_scopes") or []
    # Google OAuth 와 동일 — 번들 user_scopes 에 신규 필수 scope 가 없어도 merge
    merged = list(dict.fromkeys(stored + _FALLBACK_USER_SCOPES)) if stored else list(_FALLBACK_USER_SCOPES)
    return {
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": data.get("redirect_uri") or "http://localhost:8100/slack/callback",  # cxt-ignore: fake_data
        "user_scopes": merged,
    }


def is_configured() -> bool:
    try:
        _load_client()
        return True
    except Exception:
        return False


# 토큰 저장은 크로스플랫폼 중앙 백엔드에 위임 (pipeline/keychain.py, INT-1494) —
# macOS=Keychain, Windows=Credential Manager. 과거 `security` 직접 호출은 Windows
# 에서 OAuth 토큰 저장을 깨뜨렸다.
def keychain_save(account: str, value: str) -> None:
    if not _kc.set_secret(account, value, service=KEYCHAIN_SERVICE):
        raise RuntimeError(f"토큰 저장 실패(secure store 미가용): {account}")


def keychain_load(account: str) -> str | None:
    return _kc.get_secret(account, service=KEYCHAIN_SERVICE)


def keychain_delete(account: str) -> None:
    _kc.delete_secret(account, service=KEYCHAIN_SERVICE)


def _post_token_endpoint(params: dict[str, str]) -> dict:
    """oauth.v2.access 호출 (인증 교환·rotation 갱신 공용)."""
    import ssl
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        _TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return json.loads(r.read().decode())


def _save_rotation(authed: dict) -> None:
    """token rotation 활성 앱이면 refresh_token/expires_in 이 같이 온다 — 자동 갱신용 저장."""
    refresh = authed.get("refresh_token") or ""
    expires_in = authed.get("expires_in")
    if refresh:
        keychain_save("user_refresh", refresh)
    if expires_in:
        keychain_save("user_expires_at", str(int(time.time()) + int(expires_in)))


def refresh_user_token() -> str | None:
    """rotation refresh_token 으로 user token 갱신. 실패/미보유 시 None."""
    refresh = keychain_load("user_refresh")
    if not refresh:
        return None
    try:
        client = _load_client()
        resp = _post_token_endpoint({
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        })
    except Exception:
        return None
    if not resp.get("ok"):
        return None
    # user-token refresh 응답은 top-level access_token(token_type=user) 또는
    # authed_user 안에 올 수 있다 — 둘 다 처리.
    authed = resp.get("authed_user") or {}
    token = authed.get("access_token") or (
        resp.get("access_token") if resp.get("token_type") == "user" else None
    )
    if not token:
        return None
    keychain_save("user", token)
    _save_rotation(authed if authed.get("refresh_token") else resp)
    return token


def user_token() -> str | None:
    """유효한 Slack user token(xoxp). rotation 앱이면 만료 임박 시 자동 갱신. 없으면 None."""
    token = keychain_load("user")
    if not token:
        return None
    exp = keychain_load("user_expires_at")
    if exp:
        try:
            if time.time() >= int(exp) - 300:  # 5분 마진
                return refresh_user_token()
        except ValueError:
            pass
    return token


def stored_team() -> str | None:
    return keychain_load("team_id")


def is_authenticated() -> bool:
    return bool(user_token())


def authorize_url() -> str:
    """브라우저로 열 Slack 동의 URL. user_scope 로 user token(xoxp) 을 요청한다."""
    client = _load_client()
    state = secrets.token_urlsafe(16)
    verifier, challenge = _pkce_pair()
    _pending_state["state"] = state
    _pending_state["code_verifier"] = verifier
    params = urllib.parse.urlencode({
        "client_id": client["client_id"],
        "user_scope": ",".join(client["user_scopes"]),
        "redirect_uri": client["redirect_uri"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{_AUTHORIZE_URL}?{params}"


def exchange_code(code: str, state: str | None = None) -> dict:
    """code → oauth.v2.access → user token(xoxp) 저장.
    반환: {"ok", "user", "team", "error"}. 백엔드 GET /slack/callback 가 호출."""
    # state 정확 일치 필수 (INT-2233): 누락/pending 부재 시 통과하면 login CSRF·세션 주입.
    if not _pending_state.get("state") or state != _pending_state["state"]:
        return {"ok": False, "user": None, "team": None, "error": "state 불일치 — 보안상 중단"}
    try:
        client = _load_client()
    except SlackOAuthNotConfigured as e:
        return {"ok": False, "user": None, "team": None, "error": str(e)}

    token_params: dict[str, str] = {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "code": code,
        "redirect_uri": client["redirect_uri"],
    }
    verifier = _pending_state.get("code_verifier")
    if verifier:
        token_params["code_verifier"] = verifier
    data = urllib.parse.urlencode(token_params).encode("utf-8")
    req = urllib.request.Request(
        _TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            resp = json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "user": None, "team": None, "error": f"토큰 교환 실패: {e}"}

    if not resp.get("ok"):
        return {"ok": False, "user": None, "team": None, "error": f"Slack: {resp.get('error')}"}

    authed = resp.get("authed_user") or {}
    xoxp = authed.get("access_token", "")
    if not xoxp:
        return {"ok": False, "user": None, "team": None,
                "error": "user token(xoxp) 미수신. user_scope 가 지정됐는지 확인."}

    team_id = (resp.get("team") or {}).get("id", "")
    user_id = authed.get("id", "")
    keychain_save("user", xoxp)
    _save_rotation(authed)
    if team_id:
        keychain_save("team_id", team_id)
    if user_id:
        keychain_save("user_id", user_id)
    _pending_state.pop("state", None)
    _pending_state.pop("code_verifier", None)
    return {"ok": True, "user": user_id, "team": team_id, "error": None}


def logout() -> None:
    for a in ("user", "team_id", "user_id", "user_refresh", "user_expires_at"):
        keychain_delete(a)
