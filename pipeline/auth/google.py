#!/usr/bin/env python3
# Created: 2026-05-15 (재작성: 2026-06-08 — Slack 패턴 단일계정으로 일반화)
# Purpose: Google OAuth 2.0 — 내장 client 로 사용자가 브라우저 로그인만 하면 연결.
#   Slack(pipeline/auth/slack.py)·Superthread 패턴과 동일하게, VEGA 백엔드가
#   redirect_uri 를 소유하고(http://localhost:8100/google/callback) 내장
#   google_oauth_client.json(Desktop 앱 client)을 쓴다. 사용자는 Client ID/Secret
#   을 입력하지 않는다.
#
#   과거엔 personal/intrect 두 계정을 하드코딩하고 .env 에서 계정별 client 를
#   읽었으나(배포본·타 사용자 맥에서 작동 불가), 단일 로그인 계정으로 단순화.
#
#   저장: refresh_token → macOS Keychain (service='vega-google-oauth',
#         account='refresh_token'). 토큰은 파일에 절대 저장 안 함.
#
# Dependencies: stdlib only (urllib + json + subprocess for keychain)
#
# 개발용 CLI:
#   python -m pipeline.auth.google           # 브라우저 로그인
#   python -m pipeline.auth.google --check   # 저장된 토큰 상태

from __future__ import annotations

import argparse
import http.server
import json
import secrets
import ssl
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from pipeline.data_paths import google_oauth_client_path

KEYCHAIN_SERVICE = "vega-google-oauth"
KEYCHAIN_ACCOUNT = "refresh_token"   # 단일 계정 — 이메일 무관

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DEFAULT_REDIRECT = "http://localhost:8100/google/callback"

# 내장 client.json 이 없을 때의 폴백 scope (최소 필수).
_FALLBACK_SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.readonly",
]

_pending_state: dict[str, str] = {}


class GoogleOAuthNotConfigured(RuntimeError):
    """data/google_oauth_client.json 이 없거나 client_id/secret 이 비었을 때."""


def _load_client() -> dict:
    path = google_oauth_client_path()
    if not path or not path.exists():
        raise GoogleOAuthNotConfigured(
            "Google OAuth 내장 클라이언트(data/google_oauth_client.json)가 없습니다. "
            "이 빌드는 Google 연결을 지원하지 않습니다."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    cid = (data.get("client_id") or "").strip()
    csec = (data.get("client_secret") or "").strip()
    if not cid or not csec:
        raise GoogleOAuthNotConfigured("google_oauth_client.json 에 client_id/secret 이 비어 있습니다.")
    return {
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": data.get("redirect_uri") or _DEFAULT_REDIRECT,
        "scopes": data.get("scopes") or _FALLBACK_SCOPES,
    }


def is_configured() -> bool:
    try:
        _load_client()
        return True
    except Exception:
        return False


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ── Keychain ─────────────────────────────────────────────────────────────────

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


# ── 토큰 상태 ─────────────────────────────────────────────────────────────────

def stored_refresh_token() -> str | None:
    return keychain_load(KEYCHAIN_ACCOUNT)


def stored_email() -> str | None:
    return keychain_load("email")


def is_authenticated() -> bool:
    return bool(stored_refresh_token())


# ── OAuth ────────────────────────────────────────────────────────────────────

def authorize_url(redirect_uri: str | None = None) -> str:
    """브라우저로 열 Google 동의 URL. 백엔드 GET /google/auth 가 302로 보낸다."""
    client = _load_client()
    state = secrets.token_urlsafe(16)
    redirect = redirect_uri or client["redirect_uri"]
    _pending_state["state"] = state
    _pending_state["redirect_uri"] = redirect
    params = urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(client["scopes"]),
        "access_type": "offline",
        "prompt": "consent",          # refresh_token 을 매번 받기 위해
        "state": state,
    })
    return f"{_AUTHORIZE_URL}?{params}"


def _token_request(payload: dict) -> dict:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        _TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
        return json.loads(r.read().decode())


def exchange_code(code: str, state: str | None = None) -> dict:
    """code → tokens → refresh_token Keychain 저장.
    반환: {"ok", "email", "error"}. 백엔드 GET /google/callback 가 호출."""
    if state is not None and _pending_state.get("state") and state != _pending_state["state"]:
        return {"ok": False, "email": None, "error": "state 불일치 — 보안상 중단"}
    try:
        client = _load_client()
    except GoogleOAuthNotConfigured as e:
        return {"ok": False, "email": None, "error": str(e)}

    redirect = _pending_state.get("redirect_uri") or client["redirect_uri"]
    try:
        tokens = _token_request({
            "code": code,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        })
    except Exception as e:
        return {"ok": False, "email": None, "error": f"토큰 교환 실패: {e}"}

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return {"ok": False, "email": None,
                "error": "refresh_token 미수신. 동의 화면에서 consent 가 표시됐는지 확인."}

    # 이메일 식별(선택) — access_token 으로 userinfo 조회.
    email = ""
    access_token = tokens.get("access_token")
    if access_token:
        try:
            req = urllib.request.Request(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            with urllib.request.urlopen(req, timeout=10, context=_ssl_context()) as r:
                email = (json.loads(r.read().decode()).get("email") or "")
        except Exception:
            pass

    keychain_save(KEYCHAIN_ACCOUNT, refresh_token)
    if email:
        keychain_save("email", email)
    _pending_state.pop("state", None)
    _pending_state.pop("redirect_uri", None)
    return {"ok": True, "email": email or None, "error": None}


def refresh_access_token(refresh_token: str, client_id: str, client_secret: str) -> str:
    payload = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }
    try:
        data = _token_request(payload)
        return data["access_token"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body).get("error", "")
        except Exception:
            err = ""
        if err == "invalid_grant":
            raise RuntimeError(
                "Google OAuth refresh_token expired/revoked (invalid_grant). "
                "설정에서 Google 을 다시 연결하세요."
            ) from e
        raise RuntimeError(f"Google OAuth token refresh HTTP {e.code}: {body}") from e


def get_access_token(account: str | None = None) -> str | None:
    """저장된 refresh_token → access_token. 단일 계정이라 account 인자는 무시(하위호환).

    과거 시그니처 get_access_token('personal') 호출과 호환되도록 인자를 받되,
    실제론 단일 계정 토큰을 반환한다.
    """
    refresh_token = stored_refresh_token()
    if not refresh_token:
        return None
    try:
        client = _load_client()
    except GoogleOAuthNotConfigured:
        return None
    return refresh_access_token(refresh_token, client["client_id"], client["client_secret"])


def logout() -> None:
    for a in (KEYCHAIN_ACCOUNT, "email"):
        keychain_delete(a)


# ── 개발용 CLI (브라우저 + 로컬 콜백 서버; 백엔드 없이 단독 인증) ──────────────

def do_oauth_flow() -> str | None:
    """단독 실행용 OAuth — 로컬 콜백 서버로 code 수신 후 exchange_code 호출."""
    try:
        client = _load_client()
    except GoogleOAuthNotConfigured as e:
        print(f"  {e}")
        return None

    redirect = client["redirect_uri"]
    parsed = urllib.parse.urlparse(redirect)
    port = parsed.port or 8100
    host = parsed.hostname or "localhost"

    url = authorize_url(redirect)
    received: dict = {}
    ready = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            received["code"] = q.get("code", [None])[0]
            received["state"] = q.get("state", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write("<h1>인증 완료. 이 창을 닫아주세요.</h1>".encode("utf-8"))
            ready.set()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer((host, port), Handler)

    def serve():
        while not ready.is_set():
            server.handle_request()

    threading.Thread(target=serve, daemon=True).start()

    print("\n  브라우저를 엽니다…")
    webbrowser.open(url)
    ready.wait(timeout=180)
    server.server_close()

    code = received.get("code")
    if not code:
        print("  ⚠ 인증 코드 미수신 (타임아웃/취소)")
        return None
    result = exchange_code(code, received.get("state"))
    if result.get("ok"):
        print(f"  refresh_token 저장 완료 (email={result.get('email') or '?'})")
        return stored_refresh_token()
    print(f"  ⚠ {result.get('error')}")
    return None


def keychain_check() -> None:
    tok = stored_refresh_token()
    email = stored_email()
    if tok:
        print(f"  refresh_token 저장됨 (len={len(tok)}, email={email or '?'})")
    else:
        print("  refresh_token 없음 — 먼저 인증 필요")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--get-token", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        print("Keychain 상태:")
        keychain_check()
        return 0
    if args.get_token:
        tok = get_access_token()
        print(f"access_token: {tok[:40]}..." if tok else "access_token 없음")
        return 0
    do_oauth_flow()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
