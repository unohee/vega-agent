#!/usr/bin/env python3
# Created: 2026-05-15 (재작성: 2026-06-08 — Slack 패턴 단일계정으로 일반화)
# Purpose: Google OAuth 2.0 — 내장 client 로 사용자가 브라우저 로그인만 하면 연결.
#   Slack(pipeline/auth/slack.py)·Superthread 패턴과 동일하게, VEGA 백엔드가
#   redirect_uri 를 소유하고(http://localhost:8100/google/callback) 내장  # cxt-ignore: fake_data
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

from pipeline import keychain as _kc
from pipeline.data_paths import google_oauth_client_path

KEYCHAIN_SERVICE = "vega-google-oauth"
KEYCHAIN_ACCOUNT = "refresh_token"   # 레거시/기본 계정 미러 슬롯 — 단일계정 경로 하위호환
# 멀티계정 (INT-1471): 계정별 토큰은 refresh_token::<email> 슬롯에, 연결 목록은
# accounts 슬롯(JSON 배열)에 저장. index[0]이 기본 계정이며 레거시 슬롯에 미러된다.
_ACCOUNTS_INDEX_SLOT = "accounts"
_TOKEN_SLOT_PREFIX = "refresh_token::"

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
    # 문서 생성/편집 (INT-1885: docs_create 가 documents scope 없어 403 이던 것).
    "https://www.googleapis.com/auth/documents",
    # 앱이 만든 파일 생성·관리 (KOMCA 가이드라인 등 문서 저장). drive.file 은 앱 생성
    # 파일만 접근하는 non-restricted scope라 full drive 보다 동의·검증이 가볍다.
    "https://www.googleapis.com/auth/drive.file",
]

_pending_state: dict[str, str] = {}


class GoogleOAuthNotConfigured(RuntimeError):
    """data/google_oauth_client.json 이 없거나 client_id/secret 이 비었을 때."""


# BYO(Bring-Your-Own) OAuth client — 사용자가 자기 GCP 프로젝트에서 만든 Desktop
# 클라이언트의 client_id/secret 을 Keychain 에 저장해 둔 슬롯. 이게 있으면 우선 사용한다.
# 내장 VEGA 클라이언트는 미검증 앱이라 타 사용자에게 "확인되지 않은 앱" 경고가 뜨고
# gmail.modify/drive.readonly(restricted)는 CASA 유료 감사 없이는 일반 배포 불가다.
# BYO 는 사용자 본인 앱(self-use)이라 검증·CASA 가 면제된다 → 기본 경로로 권장.
BYO_CLIENT_ID_SLOT = "byo_client_id"
BYO_CLIENT_SECRET_SLOT = "byo_client_secret"


def _byo_client() -> dict | None:
    """Keychain 에 저장된 사용자 BYO 클라이언트. 없으면 None."""
    cid = (_kc.get_secret(BYO_CLIENT_ID_SLOT, service=KEYCHAIN_SERVICE) or "").strip()
    csec = (_kc.get_secret(BYO_CLIENT_SECRET_SLOT, service=KEYCHAIN_SERVICE) or "").strip()
    if not cid or not csec:
        return None
    return {
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": _DEFAULT_REDIRECT,
        "scopes": _FALLBACK_SCOPES,
        "_source": "byo",
    }


def _builtin_client() -> dict | None:
    """번들된 내장 VEGA 클라이언트(data/google_oauth_client.json). 없거나 비면 None."""
    path = google_oauth_client_path()
    if not path or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    cid = (data.get("client_id") or "").strip()
    csec = (data.get("client_secret") or "").strip()
    if not cid or not csec:
        return None
    stored = data.get("scopes") or []
    # Merge stored scopes with fallback so newly-added required scopes (e.g.
    # documents, drive.file) are always included even on older client.json files.
    merged = list(dict.fromkeys(stored + _FALLBACK_SCOPES))
    return {
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": data.get("redirect_uri") or _DEFAULT_REDIRECT,
        "scopes": merged,
        "_source": "builtin",
    }


def _load_client() -> dict:
    """OAuth 클라이언트 해석. BYO(사용자 자기 GCP 앱) 우선 → 내장 VEGA 앱 폴백.
    둘 다 없으면 GoogleOAuthNotConfigured."""
    client = _byo_client() or _builtin_client()
    if client is None:
        raise GoogleOAuthNotConfigured(
            "Google OAuth 클라이언트가 없습니다. 설정에서 본인 GCP 프로젝트의 "
            "client_id/secret 을 입력하거나, 내장 클라이언트가 포함된 빌드를 사용하세요."
        )
    return client


def is_configured() -> bool:
    try:
        _load_client()
        return True
    except Exception:
        return False


def client_source() -> str:
    """현재 어떤 클라이언트로 동작하는지: 'byo' | 'builtin' | 'none'. UI 표시용."""
    c = _byo_client() or _builtin_client()
    return c["_source"] if c else "none"


def _current_client_id() -> str:
    """현재 effective client_id (BYO 우선). 없으면 빈 문자열."""
    c = _byo_client() or _builtin_client()
    return c["client_id"] if c else ""


def _invalidate_tokens_if_client_changed(prev_client_id: str) -> bool:
    """effective client_id 가 바뀌었으면 저장된 토큰을 전부 무효화한다.
    Google refresh_token 은 발급 client_id 에 종속 — 클라이언트가 바뀌면 그 토큰으로는
    refresh 가 invalid_grant 로 깨진다. 그런데 is_authenticated() 는 토큰 존재만 보므로
    UI 는 "연결됨"인데 모든 호출이 실패하는 좀비 상태가 된다. 바뀌면 disconnect 로
    토큰을 비워 정직하게 "연결 안 됨"으로 만들고 재인증을 유도한다.
    반환: 토큰을 무효화했으면 True."""
    new_id = _current_client_id()
    if prev_client_id and new_id != prev_client_id and is_authenticated():
        disconnect(None)   # 모든 계정 토큰 슬롯 wipe (refresh_token/email/accounts)
        return True
    return False


def save_byo_client(client_id: str, client_secret: str) -> bool:
    """사용자 BYO 클라이언트 저장(Keychain). 빈 값이면 ValueError.
    client_secret.json 형식 검증은 호출부(라우터)에서 — 여기선 값만 받는다.
    client_id 가 기존과 달라지면 종속된 토큰을 무효화한다(재인증 필요).
    반환: 토큰이 무효화됐으면 True(UI 가 "재연결 필요"로 안내)."""
    cid = (client_id or "").strip()
    csec = (client_secret or "").strip()
    if not cid or not csec:
        raise ValueError("client_id / client_secret 이 비어 있습니다.")
    prev_id = _current_client_id()
    if not _kc.set_secret(BYO_CLIENT_ID_SLOT, cid, service=KEYCHAIN_SERVICE):
        raise RuntimeError("client_id 저장 실패(secure store 미가용)")
    if not _kc.set_secret(BYO_CLIENT_SECRET_SLOT, csec, service=KEYCHAIN_SERVICE):
        # partial-write 롤백 (INT-2233) — id 를 무조건 삭제하면 기존에 동작하던 BYO 설정이
        # 파괴된다. 이전 client_id 가 있으면 복원하고, 없을 때만 삭제한다.
        if prev_id:
            _kc.set_secret(BYO_CLIENT_ID_SLOT, prev_id, service=KEYCHAIN_SERVICE)
        else:
            _kc.delete_secret(BYO_CLIENT_ID_SLOT, service=KEYCHAIN_SERVICE)
        raise RuntimeError("client_secret 저장 실패(secure store 미가용)")
    return _invalidate_tokens_if_client_changed(prev_id)


def clear_byo_client() -> bool:
    """BYO 클라이언트 제거 → 내장 클라이언트로 폴백(있으면).
    effective client_id 가 바뀌면(BYO→builtin) 종속 토큰 무효화. 반환: 무효화 여부."""
    prev_id = _current_client_id()
    _kc.delete_secret(BYO_CLIENT_ID_SLOT, service=KEYCHAIN_SERVICE)
    _kc.delete_secret(BYO_CLIENT_SECRET_SLOT, service=KEYCHAIN_SERVICE)
    return _invalidate_tokens_if_client_changed(prev_id)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ── Keychain ─────────────────────────────────────────────────────────────────

# 토큰 저장은 크로스플랫폼 중앙 백엔드에 위임 — macOS=Keychain, Windows=Credential
# Manager, Linux=Secret Service (pipeline/keychain.py, INT-1494). 과거엔 여기서
# `security` CLI 를 직접 호출해 Windows 에서 OAuth 토큰 저장이 깨졌다.
def keychain_save(account: str, value: str) -> None:
    if not _kc.set_secret(account, value, service=KEYCHAIN_SERVICE):
        raise RuntimeError(f"토큰 저장 실패(secure store 미가용): {account}")


def keychain_load(account: str) -> str | None:
    return _kc.get_secret(account, service=KEYCHAIN_SERVICE)


def keychain_delete(account: str) -> bool:
    """항목 삭제. 원래 없으면 멱등 성공(True). 실제 삭제 실패는 False —
    호출 측이 확정 상태를 보고해야 한다 (INT-1471). 백엔드가 멱등 처리."""
    return _kc.delete_secret(account, service=KEYCHAIN_SERVICE)


# ── 멀티계정 인덱스 (INT-1471) ────────────────────────────────────────────────

def _load_account_index() -> list[str]:
    """accounts 슬롯의 JSON 배열(이메일 목록). 없거나 깨지면 빈 리스트."""
    raw = keychain_load(_ACCOUNTS_INDEX_SLOT)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [str(e) for e in data if isinstance(e, str) and e.strip()]


def _save_account_index(emails: list[str]) -> None:
    keychain_save(_ACCOUNTS_INDEX_SLOT, json.dumps(emails, ensure_ascii=False))


def _token_slot(email: str) -> str:
    return _TOKEN_SLOT_PREFIX + email


def _ensure_account_index() -> list[str]:
    """인덱스 보장. 비어있는데 기존 단일 슬롯에 토큰이 있으면(구버전 설치)
    그 토큰을 기본 계정으로 마이그레이션해 인덱스에 등록한다."""
    emails = _load_account_index()
    if emails:
        return emails
    token = stored_refresh_token()
    if not token:
        return []
    email = stored_email() or "default"
    keychain_save(_token_slot(email), token)
    _save_account_index([email])
    return [email]


def stored_accounts() -> list[dict]:
    """연결된 Google 계정 목록 — [{'email','is_default'}]. index[0]이 기본 계정."""
    return [{"email": e, "is_default": i == 0} for i, e in enumerate(_ensure_account_index())]


def _resolve_account_email(account: str | None, strict: bool = False) -> str | None:
    """계정 식별자(이메일 · user_profile key('personal' 등) · 로컬파트) → 인덱스의 이메일.
    account 미지정이면 기본(첫) 계정. strict=True 면 명시 account 미일치 시 None(fail closed),
    strict=False(레거시)면 미일치도 기본 계정. 연결 계정이 없으면 None."""
    emails = _ensure_account_index()
    if not emails:
        return None
    if account:
        a = account.strip().lower()
        for e in emails:
            if e.lower() == a:
                return e
        try:
            from pipeline.user_profile import email_accounts
            for acc in email_accounts():
                if (acc.get("key") or "").lower() == a:
                    target = (acc.get("email") or "").lower()
                    for e in emails:
                        if e.lower() == target:
                            return e
        except Exception:
            pass
        for e in emails:
            if e.split("@")[0].lower() == a:
                return e
        if strict:
            # 명시 account 가 어디에도 안 맞으면 기본 계정으로 떨어지지 않는다 (INT-2233):
            # 오타/stale account 키로 엉뚱한 사용자 토큰을 쓰는 것을 막는다.
            return None
    return emails[0]


def stored_refresh_token_for(account: str | None = None) -> str | None:
    """계정 지정 토큰 조회. account 미지정이면 레거시 단일 슬롯 경로(하위호환).

    account 가 명시되면 fail-closed (INT-2233): 미해결/토큰 없음이면 기본 계정으로
    fallback 하지 않고 None — 명시 계정이 아닌 다른 사용자로 실행되는 것을 방지."""
    if account is not None:
        email = _resolve_account_email(account, strict=True)
        return keychain_load(_token_slot(email)) if email else None
    return stored_refresh_token()


def disconnect(account: str | None = None) -> dict:
    """계정 연결 해제 — 삭제 후 Keychain 실측 재조회로 확정 상태 반환 (INT-1471).

    account 미지정: 전체 해제. 지정: 해당 계정만 제거(기본 계정이면 다음 계정 승격).
    반환: {"ok", "authenticated", "accounts", "error"?} — 프론트는 이 확정값을 그대로
    반영하면 되고 재폴링이 필요 없다.
    """
    emails = _ensure_account_index()
    failed: list[str] = []

    def _delete_verified(slot: str) -> bool:
        ok = keychain_delete(slot)
        return ok and keychain_load(slot) is None

    def _wipe_default_slots() -> None:
        for slot in (KEYCHAIN_ACCOUNT, "email", _ACCOUNTS_INDEX_SLOT):
            if not _delete_verified(slot):
                failed.append(slot)

    if account is None:
        for e in emails:
            if not _delete_verified(_token_slot(e)):
                failed.append(e)
        _wipe_default_slots()
    else:
        a = account.strip().lower()
        email = next((e for e in emails if e.lower() == a), None)
        if email is None:
            return {"ok": False, "authenticated": is_authenticated(),
                    "accounts": stored_accounts(),
                    "error": f"연결되지 않은 계정: {account}"}
        if not _delete_verified(_token_slot(email)):
            failed.append(email)
        remaining = [e for e in emails if e != email]
        was_default = emails[0] == email
        if remaining:
            _save_account_index(remaining)
            if was_default:
                # 다음 계정을 기본으로 승격 — 레거시 미러·email 슬롯 동기화
                token = keychain_load(_token_slot(remaining[0]))
                if token:
                    keychain_save(KEYCHAIN_ACCOUNT, token)
                    keychain_save("email", remaining[0])
        else:
            _wipe_default_slots()

    out = {"ok": not failed, "authenticated": is_authenticated(), "accounts": stored_accounts()}
    if failed:
        out["error"] = "Keychain 삭제 실패: " + ", ".join(failed)
    return out


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
        # consent: refresh_token 을 매번 받기 위해 / select_account: 멀티계정 추가 시
        # 이미 로그인된 계정으로 자동 통과하지 않고 계정을 고르게 (INT-1471)
        "prompt": "consent select_account",
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
    # state 정확 일치 필수 (INT-2233): 누락/pending 부재 시 통과하면 login CSRF·세션 주입.
    if not _pending_state.get("state") or state != _pending_state["state"]:
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

    # 멀티계정 등록 (INT-1471) — userinfo 이메일을 라벨로 사용 (하드코딩 금지).
    # 이메일 조회 실패 시 'default' 라벨로 등록.
    label = email or "default"
    emails = _ensure_account_index()  # 구버전 단일 슬롯이 있으면 먼저 기본 계정으로 인식
    if label not in emails:
        emails = emails + [label]
    keychain_save(_token_slot(label), refresh_token)
    _save_account_index(emails)
    # 기본 계정(index[0])이면 레거시 단일 슬롯 미러 — 구버전 호출 경로 호환
    if emails[0] == label:
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
        if "access_token" not in data:
            raise RuntimeError(f"Google OAuth 응답에 access_token 없음: {data}")
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
    """저장된 refresh_token → access_token.

    account에 이메일·user_profile key('personal' 등)를 주면 해당 계정 토큰 (INT-1471 멀티계정).
    미지정·미일치 시 기본(첫) 계정 토큰 — 과거 단일계정 호출과 호환.
    """
    refresh_token = stored_refresh_token_for(account)
    if not refresh_token:
        return None
    try:
        client = _load_client()
    except GoogleOAuthNotConfigured:
        return None
    return refresh_access_token(refresh_token, client["client_id"], client["client_secret"])


def logout() -> None:
    """전체 연결 해제 — disconnect(None)와 동일 (하위호환 유지)."""
    disconnect(None)


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
        print(f"  refresh_token 저장 완료 (email={result.get('email') or '?'})")  # cxt-ignore: fake_execution
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
