# Created: 2026-06-24
# Purpose: network router (INT-1372) — 라우터 등록·PrivateKey 비노출 회귀 잠금
# Dependencies: pytest, fastapi.testclient, web.routers.network
# Test Status: green

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from web.routers import network


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(network.router)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_status_returns_200(client):
    resp = client.get("/api/network/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "tailscale" in data
    assert "wireguard" in data


def test_wireguard_get_never_returns_private_key(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VEGA_WIREGUARD_DIR", str(tmp_path))
    resp = client.get("/api/network/wireguard")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("private_key") is None
    assert data.get("private_key_redacted") is True


def test_wireguard_post_does_not_echo_private_key(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VEGA_WIREGUARD_DIR", str(tmp_path))
    resp = client.post(
        "/api/network/wireguard",
        json={"PrivateKey": "s3cr3t_key", "Address": "10.0.0.2/32"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("private_key") is None
    assert data.get("private_key_redacted") is True


def test_wireguard_post_writes_key_file(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VEGA_WIREGUARD_DIR", str(tmp_path))
    client.post(
        "/api/network/wireguard",
        json={"PrivateKey": "my_private_key"},
    )
    key_file = tmp_path / "privatekey"
    assert key_file.exists()
    assert key_file.read_text().strip() == "my_private_key"


def test_wireguard_post_writes_publickey_when_wg_available(client, tmp_path, monkeypatch):
    """private key 저장 시 wg pubkey 로 publickey 파일 갱신 (Bugbot)."""
    import shutil
    if not shutil.which("wg"):
        pytest.skip("wg CLI not installed")
    monkeypatch.setenv("VEGA_WIREGUARD_DIR", str(tmp_path))
    # WireGuard 표준 테스트 키 쌍 (공개 문서용 dummy)
    priv = "YFWEF9M7XDG7ZRNMHYXHGYGVDXTHCHMYXQQ2THTHCH2MHYUGLHQ="
    r = subprocess.run(["wg", "pubkey"], input=priv + "\n", text=True, capture_output=True, timeout=5)
    if r.returncode != 0:
        pytest.skip("wg pubkey failed")
    expected_pub = r.stdout.strip()
    client.post("/api/network/wireguard", json={"PrivateKey": priv})
    pub_file = tmp_path / "publickey"
    assert pub_file.exists()
    assert pub_file.read_text().strip() == expected_pub
    resp = client.get("/api/network/wireguard")
    assert resp.json().get("public_key") == expected_pub


def test_wireguard_post_writes_conf_file(client, tmp_path, monkeypatch):
    monkeypatch.setenv("VEGA_WIREGUARD_DIR", str(tmp_path))
    client.post(
        "/api/network/wireguard",
        json={
            "Address": "10.0.0.3/32",
            "DNS": "1.1.1.1",
            "PublicKey": "peer_pub_key",
            "AllowedIPs": "0.0.0.0/0",
            "Endpoint": "vpn.example.com:51820",
        },
    )
    conf = (tmp_path / "client.conf").read_text()
    assert "Address = 10.0.0.3/32" in conf
    assert "DNS = 1.1.1.1" in conf
    assert "PublicKey = peer_pub_key" in conf
    assert "PrivateKey" not in conf  # PrivateKey 없이도 conf 작성 가능
