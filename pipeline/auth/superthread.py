# Created: 2026-06-08
# Purpose: Superthread OAuth (Authorization Code + PKCE) → PAT 자동 발급.
#   kyte-portal(kyte_cli/web/superthread_oauth.py)의 검증된 구현을 vega-agent 로
#   포팅. Slack(pipeline/auth/slack.py) 패턴에 맞춰 단일 사용자 Keychain 저장.
#
#   Superthread 는 public client(ocstcli, client_secret 없음)라서 PKCE 만으로
#   인증한다. OAuth access_token 으로 곧장 API 를 쓰지 않고, 365일 PAT 를
#   발급(/auth/{workspace}/pats)해 Keychain 에 저장한다 — st CLI 와 동일.
#
#   흐름:
#     1. GET /superthread/auth  → authorize_url() 로 동의 페이지 (redirect 302)
#     2. 사용자 로그인 → GET /superthread/callback?code=&state=
#        → exchange_code(): PKCE 토큰 교환 → PAT 발급 → Keychain 저장
#
#   redirect_uri 는 백엔드 라우트(http://localhost:8100/superthread/callback).  # cxt-ignore: fake_data
#   127.0.0.1 임의 포트도 Superthread 가 허용함(검증됨).
# Dependencies: stdlib only (urllib + json + subprocess for keychain)

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import ssl
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone

KEYCHAIN_SERVICE = "vega-superthread-oauth"

# Superthread 공식 CLI 가 쓰는 public OAuth client (client_secret 없음).
CLIENT_ID = "ocstcli"
SCOPE = "offline_access"
PAT_NAME = "vega-agent"
PAT_EXPIRES_DAYS = 365

# Superthread API 는 Cloudflare 뒤에 있어 User-Agent 없는 요청을 봇으로 보고
# 차단한다(403, "error code: 1010"). 모든 요청에 UA 를 명시해야 한다.
_UA = "vega-agent/0.1.10 (+https://github.com/unohee/vega-agent)"

_AUTHORIZE_URL = "https://app.superthread.com/oauth2/authorize/"
_TOKEN_URL = "https://api.superthread.com/oauth2/token"
_API_BASE = "https://api.superthread.com/v1"
# Superthread OAuth client(ocstcli)는 콜백 경로가 정확히 /callback 이어야 하고
# 호스트는 127.0.0.1(+임의 포트)만 허용한다. localhost·하위경로는 거부됨.
_DEFAULT_REDIRECT = "http://127.0.0.1:8100/callback"

_pending_state: dict[str, str] = {}


def _pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) 생성. S256 방식."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


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

def pat_token() -> str | None:
    """저장된 PAT. 만료됐으면 None."""
    token = keychain_load("pat")
    if not token:
        return None
    expires_at = keychain_load("pat_expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= exp:
                return None
        except Exception:
            pass
    return token


def stored_workspace_id() -> str | None:
    """PAT 발급에 사용한 사용자 워크스페이스(team) ID."""
    return keychain_load("workspace_id")


def is_authenticated() -> bool:
    return bool(pat_token())


# ── OAuth ────────────────────────────────────────────────────────────────────

def authorize_url(redirect_uri: str | None = None) -> str:
    """브라우저로 열 Superthread 동의 URL. PKCE(S256) Authorization Code 플로우."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    redirect = redirect_uri or _DEFAULT_REDIRECT
    _pending_state["state"] = state
    _pending_state["code_verifier"] = verifier
    _pending_state["redirect_uri"] = redirect
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": redirect,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{_AUTHORIZE_URL}?{params}"


def _open_with_error_body(req: urllib.request.Request) -> dict:
    """urlopen + HTTPError 본문 보존 — 'HTTP Error 400'만 남고 원인이 사라지는 것을 막는다."""
    import urllib.error
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


def _post_form(url: str, data: dict, headers: dict | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    hdrs = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": _UA,
        "Accept": "application/json",
    }
    if headers:
        hdrs.update(headers)
    return _open_with_error_body(urllib.request.Request(url, data=body, headers=hdrs))


def _get_json(url: str, headers: dict | None = None) -> dict:
    hdrs = {"User-Agent": _UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    return _open_with_error_body(urllib.request.Request(url, headers=hdrs))


def _discover_team_ids(access_token: str) -> list[str]:
    """사용자가 속한 워크스페이스(team) ID 목록 — GET /v1/users/me 의 user.teams[].

    과거엔 개발자 워크스페이스 ID 가 하드코딩돼 타 사용자의 PAT 발급이
    항상 실패했다(INT-1451)."""
    try:
        resp = _get_json(
            f"{_API_BASE}/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except Exception:
        return []
    teams = (resp.get("user") or {}).get("teams") or []
    return [str(t["id"]) for t in teams if t.get("id")]


def _discover_workspace_id(access_token: str) -> str | None:
    """첫 번째 소속 워크스페이스 ID (도구 폴백용 — tools_superthread._workspace_id)."""
    ids = _discover_team_ids(access_token)
    return ids[0] if ids else None


def _post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    hdrs = {
        "Content-Type": "application/json",
        "User-Agent": _UA,
        "Accept": "application/json",
    }
    if headers:
        hdrs.update(headers)
    return _open_with_error_body(urllib.request.Request(url, data=body, headers=hdrs))


def exchange_code(code: str, state: str | None = None) -> dict:
    """code → PKCE 토큰 교환 → access_token → PAT 발급 → Keychain 저장.
    반환: {"ok", "expires_at", "error"}. 백엔드 GET /superthread/callback 가 호출."""
    if state is not None and _pending_state.get("state") and state != _pending_state["state"]:
        return {"ok": False, "expires_at": None, "error": "state 불일치 — 보안상 중단"}

    verifier = _pending_state.get("code_verifier")
    redirect = _pending_state.get("redirect_uri") or _DEFAULT_REDIRECT
    if not verifier:
        return {"ok": False, "expires_at": None,
                "error": "code_verifier 없음 — authorize_url() 을 먼저 호출하세요."}

    # 1. PKCE 토큰 교환
    try:
        token_resp = _post_form(_TOKEN_URL, {
            "client_id": CLIENT_ID,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect,
        })
    except Exception as e:
        return {"ok": False, "expires_at": None, "error": f"토큰 교환 실패: {e}"}

    access_token = token_resp.get("access_token")
    if not access_token:
        return {"ok": False, "expires_at": None,
                "error": f"access_token 미수신: {json.dumps(token_resp)[:200]}"}

    # 2. 사용자 워크스페이스 발견 — PAT 는 워크스페이스 단위로 발급된다.
    team_ids = _discover_team_ids(access_token)
    if not team_ids:
        return {"ok": False, "expires_at": None,
                "error": "워크스페이스 발견 실패 — Superthread 계정에 소속된 팀이 없거나 users/me 조회가 거부됨."}

    # 3. PAT 발급 (365일) — 첫 팀이 거부하면(게스트 멤버십 등) 다음 팀에 순차 시도.
    pat_resp: dict | None = None
    workspace_id = ""
    errors: list[str] = []
    for ws in team_ids:
        try:
            pat_resp = _post_json(
                f"{_API_BASE}/auth/{ws}/pats",
                {"name": PAT_NAME, "expires_in": PAT_EXPIRES_DAYS},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            workspace_id = ws
            break
        except Exception as e:
            errors.append(f"{ws}: {e}")
    if pat_resp is None:
        return {"ok": False, "expires_at": None,
                "error": "PAT 발급 실패 — " + " / ".join(errors)}

    token = pat_resp.get("token") or (pat_resp.get("pat") or {}).get("token")
    if not token:
        return {"ok": False, "expires_at": None,
                "error": f"PAT 토큰 미수신: {json.dumps(pat_resp)[:200]}"}

    # time_expires: Unix timestamp(STime) → ISO 문자열
    expires_at: str | None = None
    raw_exp = (pat_resp.get("pat") or pat_resp).get("time_expires")
    if raw_exp:
        try:
            expires_at = datetime.fromtimestamp(int(raw_exp), tz=timezone.utc).isoformat()
        except Exception:
            pass

    keychain_save("pat", token)
    keychain_save("workspace_id", workspace_id)
    if expires_at:
        keychain_save("pat_expires_at", expires_at)
    _pending_state.pop("state", None)
    _pending_state.pop("code_verifier", None)
    _pending_state.pop("redirect_uri", None)
    return {"ok": True, "expires_at": expires_at, "error": None}


def logout() -> None:
    for a in ("pat", "pat_expires_at", "workspace_id"):
        keychain_delete(a)
