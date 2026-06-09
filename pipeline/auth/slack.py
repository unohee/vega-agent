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
import urllib.parse
import urllib.request

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
    return {
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": data.get("redirect_uri") or "http://localhost:8100/slack/callback",  # cxt-ignore: fake_data
        "user_scopes": data.get("user_scopes") or [
            "channels:read", "channels:history", "groups:history",
            "im:history", "mpim:history", "users:read", "search:read",
        ],
    }


def is_configured() -> bool:
    try:
        _load_client()
        return True
    except Exception:
        return False


def keychain_save(account: str, value: str) -> None:
    subprocess.run(
        ["security", "add-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", account, "-w", value, "-U"],
        check=True, capture_output=True,
    )


def keychain_load(account: str) -> str | None:
    r = subprocess.run(
        ["security", "find-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def keychain_delete(account: str) -> None:
    subprocess.run(
        ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
        capture_output=True,
    )


def user_token() -> str | None:
    """저장된 Slack user token(xoxp). 없으면 None."""
    return keychain_load("user")


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
    if state is not None and _pending_state.get("state") and state != _pending_state["state"]:
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
    if team_id:
        keychain_save("team_id", team_id)
    if user_id:
        keychain_save("user_id", user_id)
    _pending_state.pop("state", None)
    _pending_state.pop("code_verifier", None)
    return {"ok": True, "user": user_id, "team": team_id, "error": None}


def logout() -> None:
    for a in ("user", "team_id", "user_id"):
        keychain_delete(a)
