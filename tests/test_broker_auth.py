# Created: 2026-06-26
# Purpose: 제네릭 paired-broker 인증 회귀 테스트 (INT-1924) — state CSRF 검증,
#          같은-사이트 강제, mcp_url 폴백, CF-Access 헤더 MCP 엔트리, 회사명 하드코딩 0.
# Dependencies: pytest, unittest.mock
# Test Status: passing

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.auth import broker as br

PAIR_URL = "https://portal.example.com/pair"
MCP_URL = "https://app.example.com/mcp"  # 같은 등록가능 도메인(example.com)
CROSS_URL = "https://evil.com/mcp"


@contextmanager
def fake_keychain(initial=None):
    store = dict(initial or {})
    with patch.object(br, "keychain_save", lambda a, v: store.__setitem__(a, v)), \
         patch.object(br, "keychain_load", lambda a: store.get(a)), \
         patch.object(br, "keychain_delete", lambda a: store.pop(a, None)):
        yield store


def setup_function(_):
    br._pending.clear()


# ── authorize_url ────────────────────────────────────────────────────────────

def test_authorize_url_builds_pairing_url():
    url = br.authorize_url(PAIR_URL, mcp_url=MCP_URL)
    assert url.startswith(PAIR_URL)
    assert "cb=" in url and "state=" in url
    assert br._pending["state"] and br._pending["pair_url"] == PAIR_URL
    assert br._pending["mcp_url"] == MCP_URL


def test_authorize_url_rejects_non_https_pair():
    import pytest
    with pytest.raises(ValueError):
        br.authorize_url("http://portal.example.com/pair")  # 외부 http 거부


def test_authorize_url_rejects_cross_site_mcp():
    import pytest
    with pytest.raises(ValueError):
        br.authorize_url(PAIR_URL, mcp_url=CROSS_URL)  # 다른 도메인 거부


def test_authorize_url_rejects_cross_site_public_suffix_collision():
    """portal.example.co.uk 와 attacker.co.uk 는 같은 사이트가 아니다."""
    import pytest
    with pytest.raises(ValueError):
        br.authorize_url("https://portal.example.co.uk/pair", mcp_url="https://attacker.co.uk/mcp")


# ── handle_callback ──────────────────────────────────────────────────────────

def test_callback_state_mismatch_rejected():
    with fake_keychain() as store:
        br.authorize_url(PAIR_URL, mcp_url=MCP_URL)
        res = br.handle_callback({"client_id": "cid", "client_secret": "sec",
                                  "mcp_url": MCP_URL, "state": "WRONG"})
    assert res["ok"] is False and "state" in res["error"]
    assert store == {}  # 아무것도 저장 안 됨


def test_callback_happy_path_stores_creds():
    with fake_keychain() as store:
        br.authorize_url(PAIR_URL, mcp_url=MCP_URL)
        state = br._pending["state"]
        res = br.handle_callback({"client_id": "cid-1", "client_secret": "sec-1",
                                  "mcp_url": MCP_URL, "label": "ACME", "state": state})
        assert res["ok"] is True, res
        assert res["label"] == "acme"  # 소문자·안전화
        creds = br.credentials()
        assert creds == {"client_id": "cid-1", "client_secret": "sec-1",
                         "mcp_url": MCP_URL, "label": "acme"}
        assert br.is_authenticated() is True
    assert br._pending == {}  # 페어링 후 state 소거


def test_callback_uses_stashed_mcp_url_when_absent():
    """포털이 콜백에 mcp_url 을 안 실으면 authorize 시 주입한 폴백을 쓴다."""
    with fake_keychain():
        br.authorize_url(PAIR_URL, mcp_url=MCP_URL)
        state = br._pending["state"]
        res = br.handle_callback({"client_id": "c", "client_secret": "s", "state": state})
        assert res["ok"] is True, res
        assert br.credentials()["mcp_url"] == MCP_URL


def test_callback_missing_mcp_url_errors():
    with fake_keychain():
        br.authorize_url(PAIR_URL)  # mcp_url 미주입
        state = br._pending["state"]
        res = br.handle_callback({"client_id": "c", "client_secret": "s", "state": state})
    assert res["ok"] is False and "mcp_url" in res["error"]


def test_callback_rejects_cross_site_mcp_url():
    with fake_keychain() as store:
        br.authorize_url(PAIR_URL, mcp_url=MCP_URL)
        state = br._pending["state"]
        res = br.handle_callback({"client_id": "c", "client_secret": "s",
                                  "mcp_url": CROSS_URL, "state": state})
    assert res["ok"] is False and "도메인" in res["error"]
    assert store == {}


# ── MCP 자동등록 엔트리 ────────────────────────────────────────────────────────

def test_broker_mcp_entry_injects_cf_access_headers():
    from pipeline import mcp_client
    with fake_keychain():
        br.authorize_url(PAIR_URL, mcp_url=MCP_URL)
        res = br.handle_callback({"client_id": "cid-9", "client_secret": "sec-9",
                                  "mcp_url": MCP_URL, "label": "kyte",
                                  "state": br._pending.get("state")})
        assert res["ok"] is True, res
        entry = mcp_client._broker_mcp_entry()
        assert entry is not None
        name, cfg = entry
        assert name == "kyte"
        assert cfg["transport"] == "http"
        assert cfg["url"] == MCP_URL
        assert cfg["headers"] == {
            "CF-Access-Client-Id": "cid-9",
            "CF-Access-Client-Secret": "sec-9",
        }


def test_broker_mcp_entry_none_when_unpaired():
    from pipeline import mcp_client
    with fake_keychain():  # 빈 store
        assert mcp_client._broker_mcp_entry() is None


# ── 하드코딩 0 / logout ────────────────────────────────────────────────────────

def test_no_company_hardcode_in_source():
    """공개 저장소 — 특정 회사 URL/이름이 broker.py 소스에 없어야 한다 (INT-1924)."""
    src = Path(br.__file__).read_text(encoding="utf-8").lower()
    for needle in ("kyte", "portal.kyte", "app.kyte"):
        assert needle not in src, f"하드코딩 발견: {needle}"


def test_logout_clears_all_slots():
    with fake_keychain({"client_id": "c", "client_secret": "s",
                        "mcp_url": MCP_URL, "label": "x"}) as store:
        assert br.is_authenticated() is True
        br.logout()
        assert store == {}
        assert br.credentials() is None


# ── FastAPI routes ──────────────────────────────────────────────────────────

def test_broker_auth_route_redirects_to_pair_url():
    pytest = __import__("pytest")
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    from web.routers.oauth import router

    app = fastapi.FastAPI()
    app.include_router(router)
    with testclient.TestClient(app, raise_server_exceptions=False) as client:
        res = client.get(
            "/broker/auth",
            params={"pair_url": PAIR_URL, "mcp_url": MCP_URL},
            follow_redirects=False,
        )
    assert res.status_code == 302
    assert res.headers["location"].startswith(PAIR_URL)


def test_broker_callback_escapes_error_html():
    pytest = __import__("pytest")
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    from web.routers.oauth import router

    app = fastapi.FastAPI()
    app.include_router(router)
    with testclient.TestClient(app, raise_server_exceptions=False) as client:
        res = client.get("/broker/callback", params={"error": "<script>alert(1)</script>"})
    assert res.status_code == 400
    assert "<script>" not in res.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in res.text
