# Created: 2026-06-26
# Purpose: 제네릭 paired-broker 인증 (INT-1924, Model A). 외부 "브로커 포털"과
#   페어링해 Cloudflare Access 서비스 토큰(client_id/secret)과 MCP 엔드포인트를
#   받아온다. 브로커가 사용자 대신 백엔드 OAuth(예: 회사 Google Workspace)를
#   서버사이드로 수행하므로 VEGA 는 Google 토큰/시크릿을 보관하지 않는다.
#
#   특정 회사·URL 을 소스에 하드코딩하지 않는다(공개 저장소 — grep 회사명 = 0):
#     - pair_url  : 호출자(온보딩/config)가 주입.
#     - mcp_url   : 페어링 콜백이 전달(또는 authorize 시 같이 주입).
#     - label     : 페어링 콜백이 전달(MCP 서버 이름·도구 프리픽스에 사용).
#   Slack/Superthread auth 모듈과 동일한 Keychain·is_authenticated() 게이트.
#
#   흐름:
#     1. GET /broker/auth?pair_url=...  → authorize_url(pair_url) (redirect 302)
#        브라우저: <pair_url>?cb=http://localhost:8100/broker/callback&state=<rand>
#     2. 포털 SSO + 동의 → GET /broker/callback?client_id=&client_secret=&mcp_url=&label=&state=
#        → handle_callback(): state 검증 + 같은-사이트 검증 → Keychain 저장
#     3. mcp_client 가 저장된 자격으로 mcp_url 을 자동 등록(CF-Access-* 헤더 주입)
# Dependencies: stdlib only (urllib) + pipeline.keychain
from __future__ import annotations

import secrets
import urllib.parse

from pipeline import keychain as _kc

KEYCHAIN_SERVICE = "vega-broker"

# 페어링 콜백이 돌아올 백엔드 라우트. 포털은 cb 파라미터로 이 주소를 받는다.
_DEFAULT_REDIRECT = "http://localhost:8100/broker/callback"

# 저장 슬롯
_SLOTS = ("client_id", "client_secret", "mcp_url", "label")

# CSRF 보호용 1회성 state(+같이 주입된 mcp_url/label). 프로세스 메모리.
_pending: dict[str, str] = {}


# ── Keychain ─────────────────────────────────────────────────────────────────
# 크로스플랫폼 중앙 백엔드에 위임(pipeline/keychain.py): macOS=Keychain,
# Windows=Credential Manager. Slack/Superthread auth 와 동일 패턴.

def keychain_save(account: str, value: str) -> None:
    if not _kc.set_secret(account, value, service=KEYCHAIN_SERVICE):
        raise RuntimeError(f"브로커 자격 저장 실패(secure store 미가용): {account}")


def keychain_load(account: str) -> str | None:
    return _kc.get_secret(account, service=KEYCHAIN_SERVICE)


def keychain_delete(account: str) -> None:
    _kc.delete_secret(account, service=KEYCHAIN_SERVICE)


# ── URL 검증 ─────────────────────────────────────────────────────────────────

def _require_https(url: str) -> str:
    """https 만 허용(테스트용 localhost 는 예외). 자격을 보낼 엔드포인트라 스킴을 강제한다."""
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower()
    is_local = host in ("localhost", "127.0.0.1", "::1")
    if p.scheme != "https" and not (p.scheme == "http" and is_local):
        raise ValueError(f"https URL 이 필요합니다(받은 값 스킴: {p.scheme!r})")
    if not host:
        raise ValueError("URL 에 호스트가 없습니다")
    return url


_COMMON_MULTI_LABEL_PUBLIC_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp",
    "co.kr", "ne.kr", "or.kr", "ac.kr", "go.kr",
    "com.br", "net.br", "org.br", "gov.br",
    "com.cn", "net.cn", "org.cn", "gov.cn",
}


def _registrable(host: str) -> str:
    """eTLD+1 근사.

    공개 접미사 목록 의존성을 추가하지 않는 대신 흔한 2-라벨 public suffix 는
    한 라벨을 더 포함한다. 예: portal.example.co.uk -> example.co.uk.
    """
    labels = (host or "").lower().rstrip(".").split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _COMMON_MULTI_LABEL_PUBLIC_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else (host or "").lower()


def _same_site(a: str, b: str) -> bool:
    """두 URL 이 같은 등록가능 도메인(eTLD+1 근사)인지. 브로커가 자격을 무관한
    호스트로 흘리는 것을 막는 방어선(예: pair=portal.example.com, mcp=app.example.com → True)."""
    ha = _registrable(urllib.parse.urlparse(a).hostname or "")
    hb = _registrable(urllib.parse.urlparse(b).hostname or "")
    return bool(ha) and ha == hb


# ── 자격 상태 ─────────────────────────────────────────────────────────────────

def credentials() -> dict | None:
    """저장된 브로커 자격 {client_id, client_secret, mcp_url, label}. 미완이면 None."""
    cid = keychain_load("client_id")
    csec = keychain_load("client_secret")
    mcp_url = keychain_load("mcp_url")
    if not (cid and csec and mcp_url):
        return None
    return {
        "client_id": cid,
        "client_secret": csec,
        "mcp_url": mcp_url,
        "label": keychain_load("label") or "broker",
    }


def is_authenticated() -> bool:
    """브로커 자격 보유 여부 — mcp_client 자동등록·온보딩 게이트가 호출."""
    return credentials() is not None


# ── 페어링 ───────────────────────────────────────────────────────────────────

def authorize_url(
    pair_url: str, mcp_url: str | None = None, redirect_uri: str | None = None
) -> str:
    """브라우저로 열 페어링 URL: <pair_url>?cb=<redirect>&state=<rand>.

    pair_url 은 회사 포털의 페어링 엔드포인트(호출자 주입, 소스 하드코딩 금지).
    mcp_url 을 같이 주면 콜백에 mcp_url 이 없을 때의 폴백으로 stash 한다
    (포털이 콜백에 mcp_url 을 실어주면 그 값이 우선).
    """
    _require_https(pair_url)
    if mcp_url is not None:
        _require_https(mcp_url)
        if not _same_site(pair_url, mcp_url):
            raise ValueError("mcp_url 이 pair_url 과 다른 도메인입니다 — 보안상 거부")
    redirect = redirect_uri or _DEFAULT_REDIRECT
    state = secrets.token_urlsafe(16)
    _pending.clear()
    _pending["state"] = state
    _pending["pair_url"] = pair_url
    _pending["redirect_uri"] = redirect
    if mcp_url:
        _pending["mcp_url"] = mcp_url
    params = urllib.parse.urlencode({"cb": redirect, "state": state})
    sep = "&" if urllib.parse.urlparse(pair_url).query else "?"
    return f"{pair_url}{sep}{params}"


def handle_callback(params: dict) -> dict:
    """페어링 콜백 처리: state 검증 → 자격 저장. 백엔드 GET /broker/callback 가 호출.

    params: client_id, client_secret, state, (mcp_url), (label).
    반환: {"ok": bool, "label": str|None, "error": str|None}.
    """
    state = params.get("state")
    if not _pending.get("state"):
        return {"ok": False, "label": None,
                "error": "진행 중인 페어링이 없음 — authorize_url() 을 먼저 호출하세요."}
    if state != _pending.get("state"):
        return {"ok": False, "label": None, "error": "state 불일치 — 보안상 중단"}

    cid = (params.get("client_id") or "").strip()
    csec = (params.get("client_secret") or "").strip()
    if not cid or not csec:
        return {"ok": False, "label": None, "error": "client_id/secret 미수신"}

    # mcp_url: 콜백 값 우선, 없으면 authorize 시 주입된 폴백.
    mcp_url = (params.get("mcp_url") or _pending.get("mcp_url") or "").strip()
    if not mcp_url:
        return {"ok": False, "label": None,
                "error": "mcp_url 미수신 — 콜백 또는 authorize_url(mcp_url=...) 로 제공해야 함"}
    try:
        _require_https(mcp_url)
    except ValueError as e:
        return {"ok": False, "label": None, "error": f"mcp_url 검증 실패: {e}"}
    # 자격이 흘러갈 MCP 호스트는 페어링한 브로커와 같은 사이트여야 한다.
    if not _same_site(_pending.get("pair_url", ""), mcp_url):
        return {"ok": False, "label": None,
                "error": "mcp_url 이 pair_url 과 다른 도메인 — 보안상 거부"}

    # label: MCP 서버 이름·도구 프리픽스. 영숫자/_/- 만 허용(도구명 안전).
    label = (params.get("label") or "broker").strip().lower()
    label = "".join(c for c in label if c.isalnum() or c in "_-") or "broker"

    keychain_save("client_id", cid)
    keychain_save("client_secret", csec)
    keychain_save("mcp_url", mcp_url)
    keychain_save("label", label)
    _pending.clear()
    return {"ok": True, "label": label, "error": None}


def logout() -> None:
    """저장된 브로커 자격 제거."""
    for slot in _SLOTS:
        keychain_delete(slot)
    _pending.clear()
