#!/usr/bin/env python3
# Created: 2026-05-15
# Purpose: Google OAuth 2.0 installed-app authentication + store refresh_token in macOS Keychain
# Dependencies: stdlib only (urllib + json + subprocess for keychain)
# Test Status: verified 2026-05-15
#
# Configured accounts:
#   - personal: unohee.official@gmail.com (GOOGLE_PERSONAL_OAUTH_CLIENT_*)
#   - company:  heewon.oh@intrect.io (GOOGLE_INTRECT_OAUTH_CLIENT_*)
#
# Storage: refresh_token → macOS Keychain (service='vega-google-oauth', account=email)
# Only reads client_id/secret from .env; tokens are never written to files
#
# Usage:
#   python -m pipeline.auth.google --account personal   # authenticate personal account
#   python -m pipeline.auth.google --account intrect    # authenticate company account
#   python -m pipeline.auth.google --check              # check stored token status

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


ENV_PATH = Path("/Users/unohee/dev/intrect-platform/.env")
KEYCHAIN_SERVICE = "vega-google-oauth"
REDIRECT_PORT = 9876
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"

SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
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
         "-U"],
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
            print(f"  [{name}] {info['email']}: refresh_token stored (len={len(token)})")
        else:
            print(f"  [{name}] {info['email']}: not found")


def exchange_code(code: str, client_id: str, client_secret: str,
                  redirect_uri: str = REDIRECT_URI) -> dict:
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
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data["access_token"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
            err = payload.get("error", "")
            desc = payload.get("error_description", body)
        except Exception:
            err, desc = "", body
        if err == "invalid_grant":
            raise RuntimeError(
                "Google OAuth refresh_token expired/revoked (invalid_grant). "
                "Re-authenticate with: python -m pipeline.auth.google --account <personal|intrect>. "
                f"Google said: {desc}"
            ) from e
        raise RuntimeError(f"Google OAuth token refresh HTTP {e.code}: {body}") from e


def do_oauth_flow(account_key: str) -> str | None:
    info = ACCOUNTS[account_key]
    env = load_env()
    client_id = env.get(info["client_id_key"])
    client_secret = env.get(info["client_secret_key"])
    if not client_id or not client_secret:
        print(f"  Could not find {info['client_id_key']} / {info['client_secret_key']} in .env")
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

    print(f"\n  Opening browser for: {info['email']}")
    webbrowser.open(auth_url)

    ready.wait(timeout=120)
    server.server_close()

    code = received.get("code")
    if not code:
        print("  ⚠ Authorization code not received (timeout or cancelled)")
        return None
    if received.get("state") != state:
        print(f"  ⚠ state mismatch — continuing anyway")

    tokens = exchange_code(code, client_id, client_secret)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("  ⚠ No refresh_token received. Check that consent prompt was shown")
        return None

    keychain_save(info["email"], refresh_token)
    print(f"\n  refresh_token saved to Keychain (service={KEYCHAIN_SERVICE}, account={info['email']})")
    return refresh_token


def get_access_token(account_key: str) -> str | None:
    info = ACCOUNTS[account_key]
    refresh_token = keychain_load(info["email"])
    if not refresh_token:
        print(f"  No refresh_token. Authenticate first: python -m pipeline.auth.google --account {account_key}")
        return None
    env = load_env()
    client_id = env[info["client_id_key"]]
    client_secret = env[info["client_secret_key"]]
    return refresh_access_token(refresh_token, client_id, client_secret)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", choices=["personal", "intrect"], default="personal")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--get-token", action="store_true")
    ap.add_argument("--code", help="authorization code (for SSH environments)")
    ap.add_argument("--url-only", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        print("Keychain storage status:")
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
        info = ACCOUNTS[args.account]
        env = load_env()
        client_id = env[info["client_id_key"]]
        client_secret = env[info["client_secret_key"]]
        tokens = exchange_code(args.code, client_id, client_secret, REDIRECT_URI)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print("  ⚠ No refresh_token received")
            return 1
        keychain_save(info["email"], refresh_token)
        print(f"  refresh_token saved to Keychain (account={info['email']})")
    else:
        do_oauth_flow(args.account)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
