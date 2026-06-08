# Created: 2026-06-08
# Purpose: web/routers/data_boundary.py — 데이터 경계 export/wipe (INT-1383)
# Dependencies: pytest, fastapi TestClient

from __future__ import annotations

import sqlite3
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.data_paths.data_dir", lambda: tmp_path)
    # 더미 자산
    (tmp_path / "agent.db").write_bytes(b"sqlite dummy")
    (tmp_path / "memory_settings.json").write_text('{"x":1}')
    (tmp_path / "patches").mkdir()
    (tmp_path / "patches" / "tool.py").write_text("def tool(): pass")
    (tmp_path / "chatgpt_token.json").write_text('{"token":"secret"}')
    import importlib
    import web.routers.data_boundary as db
    importlib.reload(db)
    app = FastAPI()
    app.include_router(db.router)
    return TestClient(app), tmp_path


class TestSummary:
    def test_summary_lists_assets(self, client):
        c, d = client
        r = c.get("/api/data/summary").json()
        names = {a["name"] for a in r["assets"]}
        assert "agent.db" in names
        assert "patches" in names
        assert r["total_bytes"] > 0

    def test_summary_marks_token_kind(self, client):
        c, d = client
        r = c.get("/api/data/summary").json()
        tok = next(a for a in r["assets"] if a["name"] == "chatgpt_token.json")
        assert tok["kind"] == "token"


class TestExport:
    def test_export_creates_zip(self, client):
        c, d = client
        r = c.post("/api/data/export", json={}).json()
        assert r["ok"] is True
        zpath = d / "exports"
        zips = list(zpath.glob("*.zip"))
        assert len(zips) == 1
        with zipfile.ZipFile(zips[0]) as zf:
            names = zf.namelist()
            assert "agent.db" in names
            assert any(n.startswith("patches/") for n in names)

    def test_export_excludes_tokens_by_default(self, client):
        c, d = client
        c.post("/api/data/export", json={})
        zips = list((d / "exports").glob("*.zip"))
        with zipfile.ZipFile(zips[0]) as zf:
            assert "chatgpt_token.json" not in zf.namelist()

    def test_export_includes_tokens_when_asked(self, client):
        c, d = client
        r = c.post("/api/data/export", json={"include_tokens": True}).json()
        assert r["tokens_included"] is True
        zips = list((d / "exports").glob("*.zip"))
        with zipfile.ZipFile(zips[0]) as zf:
            assert "chatgpt_token.json" in zf.namelist()


class TestWipe:
    def test_wipe_requires_confirm(self, client):
        c, d = client
        r = c.post("/api/data/wipe", json={})
        assert r.status_code == 400
        # agent.db는 그대로 있어야 함 (삭제 안 됨)
        assert (d / "agent.db").exists()

    def test_wipe_with_confirm_returns_result(self, client):
        c, d = client
        r = c.post("/api/data/wipe", json={"confirm": True}).json()
        # trash CLI 유무와 무관하게 removed/skipped 키 반환
        assert "removed" in r and "skipped" in r
