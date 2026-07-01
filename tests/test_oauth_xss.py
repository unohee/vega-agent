# Created: 2026-07-01
# Purpose: OAuth callback reflected XSS escape 회귀 (INT-2232 audit).
# Dependencies: web/routers/oauth.py

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routers.oauth import router

_XSS = "<script>alert(1)</script>"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("path", ["/slack/callback", "/callback", "/google/callback"])
def test_callback_error_is_escaped(client, path):
    # error 파라미터(공격자 완전 제어)가 escape 없이 반영되면 reflected XSS.
    r = client.get(path, params={"error": _XSS})
    assert r.status_code == 400
    assert "<script>" not in r.text
    assert "&lt;script&gt;" in r.text
