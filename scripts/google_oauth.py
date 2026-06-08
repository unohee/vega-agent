#!/usr/bin/env python3
# Created: 2026-05-15
# Purpose: Google OAuth 2.0 installed-app 인증 + refresh_token macOS Keychain 저장
# Dependencies: stdlib only (urllib + json + subprocess for keychain)
# Test Status: 최초 실행 전 브라우저 동의 화면 필요
#
# 사용 계정:
#   - 개인: unohee.official@gmail.com (GOOGLE_OAUTH_CLIENT_*)
#   - 회사: heewon.oh@intrect.io (GOOGLE_INTRECT_OAUTH_CLIENT_*)
#
# Scope 전략 (최소 원칙):
#   userinfo.email — 계정 확인용
#   gmail.readonly — 이메일 ingestion (추후 Task #7)
#   calendar.readonly — 일정 ingestion (추후)
#
# 저장: refresh_token → macOS Keychain (service='vega-google-oauth', account=email)
# .env의 client_id/secret만 읽고, token은 절대 파일에 저장 안 함
#
# Usage:
#   python google_oauth.py --account personal   # 개인 계정 인증
#   python google_oauth.py --account intrect    # 회사 계정 인증
#   python google_oauth.py --check              # 저장된 토큰 상태 확인

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


ENV_PATH = Path(os.environ.get("VEGA_ENV_PATH", str(Path.home() / ".vega.env")))
KEYCHAIN_SERVICE = "vega-google-oauth"
REDIRECT_PORT = 9876
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"

SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",       # 읽기 + 전송 (readonly → modify)
    "https://www.googleapis.com/auth/calendar",          # 읽기 + 쓰기 (readonly → full)
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/analytics.edit",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
]

ACCOUNTS = {
    "personal": {
        "email": "unohee.official@gmail.com",
        "client_id_key": "GOOGLE_PERSONAL_OAUTH_CLIENT_ID",
        "client_secret_key": "GOOGLE_PERSONAL_OAUTH_CLIENT_SECRET",
    },
    "intrect": {
        "email": "heewon.oh@intrect.io",
        "client_id_key": "GOOGLE_INTRECT_OAUTH_CLIENT_ID",
        "client_secret_key": "GOOGLE_INTRECT_OAUTH_CLIENT_PW",
    },
}


def load_env() -> dict[str, str]:
    env = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def keychain_save(account: str, token: str) -> None:
    subprocess.run(
        ["security", "add-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", account, "-w", token,
         "-U"],  # -U: update if exists
        check=True, capture_output=True,
    )


def keychain_load(account: str) -> str | None:
    result = subprocess.run(
        ["security", "find-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def keychain_check() -> None:
    for name, info in ACCOUNTS.items():
        token = keychain_load(info["email"])
        if token:
            print(f"  [{name}] {info['email']}: refresh_token 저장됨 (len={len(token)})")
        else:
            print(f"  [{name}] {info['email']}: 없음")


def exchange_code(code: str, client_id: str, client_secret: str,
                  redirect_uri: str = REDIRECT_URI) -> dict:
    """Authorization code → tokens."""
    payload = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def refresh_access_token(refresh_token: str, client_id: str, client_secret: str) -> str:
    """refresh_token → access_token."""
    payload = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
        return data["access_token"]


def do_oauth_flow(account_key: str) -> str | None:
    """OAuth 흐름 실행 → refresh_token 반환."""
    info = ACCOUNTS[account_key]
    env = load_env()
    client_id = env.get(info["client_id_key"])
    client_secret = env.get(info["client_secret_key"])
    if not client_id or not client_secret:
        print(f"  .env에서 {info['client_id_key']} / {info['client_secret_key']} 못 찾음")
        return None

    state = secrets.token_urlsafe(16)
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urllib.parse.urlencode({
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "login_hint": info["email"],
        })
    )

    received: dict = {}
    ready = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            received["code"] = params.get("code", [None])[0]
            received["state"] = params.get("state", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write("<h1>인증 완료. 이 창을 닫아주세요.</h1>".encode("utf-8"))
            ready.set()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", REDIRECT_PORT), CallbackHandler)

    def serve_until_ready():
        while not ready.is_set():
            server.handle_request()

    t = threading.Thread(target=serve_until_ready)
    t.daemon = True
    t.start()

    print(f"\n  브라우저가 열립니다: {info['email']}")
    webbrowser.open(auth_url)

    ready.wait(timeout=120)
    server.server_close()

    code = received.get("code")
    if not code:
        print("  ⚠ 인증 코드 미수신 (타임아웃 또는 취소)")
        return None
    if received.get("state") != state:
        print(f"  ⚠ state 불일치 (expected={state[:8]}… got={str(received.get('state',''))[:8]}…) — 계속 진행")

    tokens = exchange_code(code, client_id, client_secret)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("  ⚠ refresh_token 없음. consent prompt가 나왔는지 확인")
        return None

    keychain_save(info["email"], refresh_token)
    print(f"\n  refresh_token Keychain 저장 완료 (service={KEYCHAIN_SERVICE}, account={info['email']})")
    return refresh_token



def get_access_token(account_key: str) -> str | None:
    """Keychain에서 refresh_token 로드 → access_token 반환."""
    info = ACCOUNTS[account_key]
    refresh_token = keychain_load(info["email"])
    if not refresh_token:
        print(f"  refresh_token 없음. 먼저 인증 필요: python {__file__} --account {account_key}")
        return None
    env = load_env()
    client_id = env[info["client_id_key"]]
    client_secret = env[info["client_secret_key"]]
    return refresh_access_token(refresh_token, client_id, client_secret)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", choices=["personal", "intrect"], default="personal")
    ap.add_argument("--check", action="store_true", help="저장된 토큰 상태 확인")
    ap.add_argument("--get-token", action="store_true", help="access_token 출력 (테스트)")
    ap.add_argument("--code", help="브라우저에서 받은 authorization code (SSH 환경용)")
    ap.add_argument("--url-only", action="store_true", help="인증 URL만 출력 (SSH 환경용)")
    args = ap.parse_args(argv)

    if args.check:
        print("Keychain 저장 상태:")
        keychain_check()
        return 0

    if args.get_token:
        token = get_access_token(args.account)
        if token:
            print(f"access_token: {token[:40]}...")
        return 0

    if args.url_only:
        info = ACCOUNTS[args.account]
        env = load_env()
        client_id = env[info["client_id_key"]]
        url = (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            + urllib.parse.urlencode({
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "login_hint": info["email"],
            })
        )
        print(url)
        return 0

    if args.code:
        # SSH 환경: 브라우저에서 받은 코드를 직접 교환
        info = ACCOUNTS[args.account]
        env = load_env()
        client_id = env[info["client_id_key"]]
        client_secret = env[info["client_secret_key"]]
        tokens = exchange_code(args.code, client_id, client_secret, REDIRECT_URI)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print("  ⚠ refresh_token 없음. consent prompt가 나왔는지 확인")
            return 1
        keychain_save(info["email"], refresh_token)
        print(f"  refresh_token Keychain 저장 완료 (account={info['email']})")
    else:
        do_oauth_flow(args.account)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
