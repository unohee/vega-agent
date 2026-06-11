# Created: 2026-06-11
# Purpose: 보안 감사(INT-1468) 하드닝 회귀 — XFF 신뢰 차단, 원격 게이트(Tailscale만 허용),
#          editor 화이트리스트. 권한 판정 단일 출처(web.state) 검증.
# Dependencies: pytest, unittest.mock
# Test Status: passing

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import web.state as wstate


class _Req:
    """최소 Request 더블 — headers + client.host 만 제공."""
    def __init__(self, host: str, xff: str | None = None, key: str | None = None):
        self.headers = {}
        if xff is not None:
            self.headers["x-forwarded-for"] = xff
        if key is not None:
            self.headers["x-vega-key"] = key
        self.client = type("C", (), {"host": host})()


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch):
    monkeypatch.delenv("VEGA_TRUSTED_PROXY", raising=False)
    monkeypatch.delenv("VEGA_REMOTE_ALLOW_CIDRS", raising=False)


# ── H1: XFF 신뢰 차단 ─────────────────────────────────────────────────────────

def test_loopback_real_peer():
    assert wstate.is_loopback(_Req("127.0.0.1")) is True
    assert wstate.is_loopback(_Req("::1")) is True


def test_xff_spoof_does_not_grant_loopback():
    """버그 재현 방지: 원격 peer가 XFF=127.0.0.1을 보내도 loopback이 아니어야 한다."""
    assert wstate.is_loopback(_Req("203.0.113.5", xff="127.0.0.1")) is False


def test_trusted_proxy_optin_accepts_xff(monkeypatch):
    monkeypatch.setenv("VEGA_TRUSTED_PROXY", "1")
    assert wstate.is_loopback(_Req("203.0.113.5", xff="127.0.0.1")) is True
    # 신뢰 프록시라도 XFF가 원격이면 loopback 아님
    assert wstate.is_loopback(_Req("203.0.113.5", xff="8.8.8.8")) is False


def test_zero_address_not_loopback():
    """0.0.0.0은 바인드 와일드카드일 뿐 loopback peer가 아니다 (_LOOPBACK에서 제거됨)."""
    assert wstate.is_loopback(_Req("0.0.0.0")) is False


# ── H2: 원격 침입 차단 게이트 ─────────────────────────────────────────────────

def test_remote_random_blocked():
    assert wstate.is_remote_allowed(_Req("203.0.113.5")) is False


def test_remote_loopback_allowed():
    assert wstate.is_remote_allowed(_Req("127.0.0.1")) is True


def test_remote_tailscale_cgnat_allowed():
    """Tailscale CGNAT 대역(100.64.0.0/10) peer는 원격이라도 허용 (사용자 정책)."""
    assert wstate.is_remote_allowed(_Req("100.101.102.103")) is True
    # 경계 밖(100.128.x = /10 밖)은 차단
    assert wstate.is_remote_allowed(_Req("100.128.0.1")) is False


def test_remote_enterprise_key_allowed():
    with patch.object(wstate, "load_enterprise_keys", return_value=frozenset({"vk_abc"})):
        assert wstate.is_remote_allowed(_Req("203.0.113.5", key="vk_abc")) is True
        assert wstate.is_remote_allowed(_Req("203.0.113.5", key="vk_wrong")) is False


def test_remote_extra_cidr_allowed(monkeypatch):
    monkeypatch.setenv("VEGA_REMOTE_ALLOW_CIDRS", "10.0.0.0/8")
    assert wstate.is_remote_allowed(_Req("10.1.2.3")) is True
    assert wstate.is_remote_allowed(_Req("11.1.2.3")) is False


# ── H3: editor 화이트리스트 ───────────────────────────────────────────────────

def _fs_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.routers import fs
    app = FastAPI()
    app.include_router(fs.router)
    return TestClient(app, raise_server_exceptions=False)


def test_open_in_editor_rejects_arbitrary_binary(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    client = _fs_client()
    with patch("web.routers.fs._guard_path", return_value=f), \
         patch("subprocess.Popen") as mock_popen:
        resp = client.post("/api/fs/open_in_editor",
                           json={"path": str(f), "editor": "/bin/sh"})
    assert resp.status_code == 400
    assert "허용되지 않은" in resp.json()["error"]
    mock_popen.assert_not_called()


def test_open_in_editor_allows_whitelisted(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    client = _fs_client()
    with patch("web.routers.fs._guard_path", return_value=f), \
         patch("shutil.which", return_value="/usr/local/bin/code"), \
         patch("subprocess.Popen") as mock_popen:
        resp = client.post("/api/fs/open_in_editor",
                           json={"path": str(f), "editor": "code"})
    assert resp.status_code == 200
    args = mock_popen.call_args[0][0]
    assert args[0] == "/usr/local/bin/code"
