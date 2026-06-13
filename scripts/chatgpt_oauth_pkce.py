#!/usr/bin/env python3
# Created: 2026-05-17
# Purpose: OpenAI OAuth 2.1 PKCE 흐름 — access_token + refresh_token 획득 및 자동 갱신
# Dependencies: stdlib only (Python 3.8+)
# Test Status: verified 2026-05-17 (login OK, gpt-5.5 chat OK)
#
# OpenSwarm src/auth/oauthPkce.ts + oauthStore.ts 의 Python 포팅
# Pi/Codex 하네스가 쓰는 방식과 동일한 공식 OAuth PKCE 흐름.
# 쿠키 스크래핑 없음 — Cloudflare 차단 없음.

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Event
from typing import Optional

# ── 상수 ─────────────────────────────────────────────────────────────────────

OPENAI_AUTH_ENDPOINT  = "https://auth.openai.com/oauth/authorize"
OPENAI_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"

# ChatGPT macOS 앱 first-party client_id (바이너리 추출)
DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEFAULT_SCOPES    = "openid profile email offline_access"
CALLBACK_PORT     = 1455
CALLBACK_HOST     = "localhost"  # Pi 레포와 동일 — 127.0.0.1 아님
LOGIN_TIMEOUT_S   = 120
REFRESH_BUFFER_S  = 300  # 만료 5분 전 갱신

# 토큰 저장 위치 (data/ 는 gitignore)
TOKEN_PATH = Path(__file__).parent.parent / "data" / "openai_oauth.json"

# ── PKCE 헬퍼 ─────────────────────────────────────────────────────────────────

def _generate_code_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(96)).rstrip(b"=").decode()

def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

def _generate_state() -> str:
    return secrets.token_hex(32)

# ── 브라우저 열기 ──────────────────────────────────────────────────────────────

def _open_browser(url: str) -> None:
    # Windows: "start" 는 cmd.exe 내장 명령 — Popen(["start", url]) 은 FileNotFoundError.
    # os.startfile 이 정석. (INT-1505)
    try:
        if sys.platform == "win32":
            os.startfile(url)  # type: ignore[attr-defined]
            return
        cmd = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([cmd, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    except Exception:
        pass
    try:
        import webbrowser
        if webbrowser.open(url):
            return
    except Exception:
        pass
    print(f"[Auth] 브라우저를 자동으로 열 수 없습니다. 직접 열어주세요:\n{url}")

# ── OAuth PKCE 흐름 ───────────────────────────────────────────────────────────

def run_oauth_pkce_flow(
    client_id: str = DEFAULT_CLIENT_ID,
    port: int = CALLBACK_PORT,
    scopes: str = DEFAULT_SCOPES,
) -> dict:
    """
    PKCE 흐름 실행. 성공 시 dict 반환:
      {"access_token", "refresh_token", "expires_in", "account_id"(optional)}
    """
    redirect_uri   = f"http://localhost:{port}/auth/callback"
    code_verifier  = _generate_code_verifier()
    code_challenge = _generate_code_challenge(code_verifier)
    state          = _generate_state()

    params = urllib.parse.urlencode({
        "response_type":              "code",
        "client_id":                  client_id,
        "redirect_uri":               redirect_uri,
        "code_challenge":             code_challenge,
        "code_challenge_method":      "S256",
        "scope":                      scopes,
        "state":                      state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow":  "true",
        "originator":                 "vega",
    })
    auth_url = f"{OPENAI_AUTH_ENDPOINT}?{params}"

    result_holder: dict = {}
    done = Event()

    class _CallbackHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # 콘솔 노이즈 억제

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs     = urllib.parse.parse_qs(parsed.query)

            if parsed.path != "/auth/callback":
                self._respond(404, "Not found")
                return

            error = qs.get("error", [None])[0]
            code  = qs.get("code",  [None])[0]
            ret_state = qs.get("state", [None])[0]

            if error:
                self._respond(200, _error_html(error))
                result_holder["error"] = f"OAuth error: {error}"
                done.set()
                return

            if not code or ret_state != state:
                self._respond(400, _error_html("Invalid callback parameters"))
                result_holder["error"] = "Invalid OAuth callback"
                done.set()
                return

            # token exchange
            try:
                tokens = _exchange_code(code, code_verifier, redirect_uri, client_id)
                result_holder.update(tokens)
                self._respond(200, _success_html())
            except Exception as e:
                self._respond(500, _error_html(str(e)))
                result_holder["error"] = str(e)
            done.set()

        def _respond(self, status: int, body: str):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = http.server.HTTPServer((CALLBACK_HOST, port), _CallbackHandler)
    server.timeout = 2  # accept() timeout → allows checking done flag

    print(f"[Auth] Callback server: http://localhost:{port}")
    print("[Auth] 브라우저에서 OpenAI 로그인 페이지를 엽니다...")
    _open_browser(auth_url)

    deadline = time.time() + LOGIN_TIMEOUT_S
    while not done.is_set() and time.time() < deadline:
        server.handle_request()
    server.server_close()

    if not done.is_set():
        raise TimeoutError(f"OAuth login timed out ({LOGIN_TIMEOUT_S}s). 다시 시도하세요.")

    if "error" in result_holder:
        raise RuntimeError(result_holder["error"])

    return result_holder


def _exchange_code(
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
) -> dict:
    """authorization_code → access_token + refresh_token"""
    body = urllib.parse.urlencode({
        "grant_type":    "authorization_code",
        "code":          code,
        "code_verifier": code_verifier,
        "redirect_uri":  redirect_uri,
        "client_id":     client_id,
    }).encode()

    req = urllib.request.Request(
        OPENAI_TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()

    tokens = json.loads(raw)
    if "access_token" not in tokens:
        raise RuntimeError(f"Token exchange failed: {str(tokens)[:300]}")

    # access_token JWT에서 chatgpt_account_id 추출 (Pi 방식)
    account_id: Optional[str] = None
    try:
        parts = tokens["access_token"].split(".")
        if len(parts) == 3:
            payload = json.loads(
                base64.urlsafe_b64decode(parts[1] + "==")
            )
            auth_claim = payload.get("https://api.openai.com/auth", {})
            account_id = auth_claim.get("chatgpt_account_id")
    except Exception:
        pass

    return {
        "access_token":  tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_in":    tokens.get("expires_in", 3600),
        "account_id":    account_id,
    }

# ── 토큰 저장/로드 ─────────────────────────────────────────────────────────────

def _load_profile() -> Optional[dict]:
    if not TOKEN_PATH.exists():
        return None
    try:
        return json.loads(TOKEN_PATH.read_text())
    except Exception:
        return None

def _save_profile(profile: dict) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(profile, indent=2))
    TOKEN_PATH.chmod(0o600)  # owner read/write only

# ── 공개 API ──────────────────────────────────────────────────────────────────

def login(client_id: str = DEFAULT_CLIENT_ID, port: int = CALLBACK_PORT) -> dict:
    """
    PKCE 로그인 → 토큰 저장.
    반환: 저장된 profile dict
    """
    tokens = run_oauth_pkce_flow(client_id=client_id, port=port)

    profile = {
        "type":          "oauth",
        "provider":      "openai-gpt",
        "access_token":  tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_at":    int(time.time()) + tokens["expires_in"],
        "client_id":     client_id,
        "account_id":    tokens.get("account_id"),
        "fetched_at":    int(time.time()),
    }
    _save_profile(profile)
    print(f"[Auth] GPT OAuth 인증 완료. 저장: {TOKEN_PATH}")
    if profile["account_id"]:
        print(f"[Auth] Account ID: {profile['account_id']}")
    return profile


def ensure_valid_token() -> str:
    """
    유효한 access_token 반환. 만료 임박 시 자동 refresh.
    프로파일 없으면 login() 호출 안내 후 RuntimeError.
    """
    profile = _load_profile()
    if not profile:
        raise RuntimeError(
            f"OAuth 프로파일 없음. 먼저 로그인하세요:\n"
            f"  python {__file__} login"
        )

    now = int(time.time())
    if now < profile["expires_at"] - REFRESH_BUFFER_S:
        return profile["access_token"]

    # refresh
    print("[Auth] Access token 만료 임박 — refresh 중...")
    body = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": profile["refresh_token"],
        "client_id":     profile["client_id"],
    }).encode()

    req = urllib.request.Request(
        OPENAI_TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            tokens = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Token refresh 실패 ({e.code}). 재로그인 필요:\n"
            f"  python {__file__} login"
        ) from e

    profile["access_token"] = tokens["access_token"]
    if rt := tokens.get("refresh_token"):
        profile["refresh_token"] = rt
    profile["expires_at"] = now + tokens.get("expires_in", 3600)
    _save_profile(profile)
    print("[Auth] Token refresh 완료.")
    return profile["access_token"]


def status() -> None:
    """현재 토큰 상태 출력."""
    profile = _load_profile()
    if not profile:
        print(f"[Auth] 프로파일 없음: {TOKEN_PATH}")
        print(f"  로그인: python {Path(__file__).name} login")
        return

    now     = int(time.time())
    remains = profile["expires_at"] - now
    state   = "유효" if remains > 0 else "만료"

    print(f"[Auth] 프로파일: {TOKEN_PATH}")
    print(f"  provider   : {profile.get('provider')}")
    print(f"  account_id : {profile.get('account_id', '알 수 없음')}")
    print(f"  상태       : {state} (남은 시간: {max(remains, 0)//60}분)")
    print(f"  access_token: {profile['access_token'][:40]}...")


# ── HTML 템플릿 ───────────────────────────────────────────────────────────────

def _success_html() -> str:
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>VEGA Auth</title>
<style>
body{font-family:system-ui;display:flex;justify-content:center;align-items:center;
     height:100vh;margin:0;background:#f0fdf4}
.card{text-align:center;padding:2rem;border-radius:12px;background:white;
      box-shadow:0 2px 8px rgba(0,0,0,.1)}
h1{color:#16a34a;margin-bottom:.5rem}p{color:#666}
</style></head>
<body><div class="card">
<h1>✓ 인증 완료</h1>
<p>VEGA에 OpenAI OAuth 인증이 완료되었습니다.<br>이 창을 닫아도 됩니다.</p>
</div></body></html>"""


def _error_html(error: str) -> str:
    safe = error.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>VEGA Auth Error</title>
<style>
body{{font-family:system-ui;display:flex;justify-content:center;align-items:center;
     height:100vh;margin:0;background:#fef2f2}}
.card{{text-align:center;padding:2rem;border-radius:12px;background:white;
       box-shadow:0 2px 8px rgba(0,0,0,.1)}}
h1{{color:#dc2626;margin-bottom:.5rem}}p{{color:#666}}
code{{background:#f3f4f6;padding:.2rem .5rem;border-radius:4px;font-size:.9rem}}
</style></head>
<body><div class="card">
<h1>✗ 인증 실패</h1>
<p><code>{safe}</code></p>
<p>터미널에서 다시 시도하세요.</p>
</div></body></html>"""


# ── API 호출 ─────────────────────────────────────────────────────────────────

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_CODEX_MODEL = "gpt-5.5"

def chat(
    prompt: str,
    model: str = DEFAULT_CODEX_MODEL,
    system: str = "You are a helpful assistant.",
    temperature: float = 0.3,
) -> str:
    """
    PKCE 토큰으로 ChatGPT backend-api/codex/responses 호출 (Pi/SSE 방식).
    stream=True SSE → delta 누적 후 반환.
    """
    profile = _load_profile()
    if not profile:
        raise RuntimeError("프로파일 없음. 먼저 login 하세요.")
    access_token = ensure_valid_token()
    account_id   = profile.get("account_id", "")

    payload = json.dumps({
        "model":        model,
        "instructions": system,
        "input":        [{"role": "user", "content": prompt}],
        "store":        False,
        "stream":       True,
    }).encode()

    req = urllib.request.Request(
        CODEX_BASE_URL,
        data=payload,
        headers={
            "Content-Type":       "application/json",
            "Authorization":      f"Bearer {access_token}",
            "chatgpt-account-id": account_id,
            "originator":         "vega",
            "OpenAI-Beta":        "responses=experimental",
            "accept":             "text/event-stream",
        },
        method="POST",
    )
    text = ""
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            chunk = line[6:]
            if chunk == "[DONE]":
                break
            try:
                ev = json.loads(chunk)
                if ev.get("type") == "response.output_text.delta":
                    text += ev.get("delta", "")
            except Exception:
                pass
    return text


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "login":
        try:
            login()
        except (TimeoutError, RuntimeError) as e:
            print(f"[Auth] 오류: {e}")
            sys.exit(1)

    elif cmd == "status":
        status()

    elif cmd == "token":
        try:
            print(ensure_valid_token())
        except RuntimeError as e:
            print(f"[Auth] {e}")
            sys.exit(1)

    elif cmd == "refresh":
        try:
            profile = _load_profile()
            if not profile:
                print("[Auth] 프로파일 없음. 먼저 login 하세요.")
                sys.exit(1)
            # 강제 만료 처리 후 ensure_valid_token
            profile["expires_at"] = 0
            _save_profile(profile)
            token = ensure_valid_token()
            print(f"[Auth] 새 token: {token[:40]}...")
        except RuntimeError as e:
            print(f"[Auth] {e}")
            sys.exit(1)

    else:
        print(f"Usage: python {Path(__file__).name} [login|status|token|refresh]")
