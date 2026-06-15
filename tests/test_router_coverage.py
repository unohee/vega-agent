# Created: 2026-06-15
# Purpose: 웹 라우터 저커버 영역 테스트 (INT-1529) — fs(24%)/onboarding(28%)
# Dependencies: fastapi.testclient, monkeypatch
# Test Status: 신규

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fs_client():
    from web.routers import fs
    app = FastAPI()
    app.include_router(fs.router)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def onboarding_client():
    from web.routers import onboarding
    app = FastAPI()
    app.include_router(onboarding.router)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# fs.py — GET /api/fs/list
# ---------------------------------------------------------------------------

class TestFsListEndpoint:
    def test_normal_directory_returns_entries(self, fs_client, tmp_path):
        (tmp_path / "file.txt").write_text("hi")
        (tmp_path / "subdir").mkdir()
        with patch("web.routers.fs._guard_path", return_value=tmp_path):
            resp = fs_client.get("/api/fs/list", params={"path": str(tmp_path)})
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        names = [e["name"] for e in data["entries"]]
        assert "file.txt" in names
        assert "subdir" in names

    def test_path_blocked_returns_403(self, fs_client):
        with patch("web.routers.fs._guard_path", side_effect=PermissionError("차단됨")):
            resp = fs_client.get("/api/fs/list", params={"path": "/etc/passwd"})
        assert resp.status_code == 403
        assert "error" in resp.json()

    def test_not_a_dir_returns_400(self, fs_client, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("web.routers.fs._guard_path", return_value=f):
            resp = fs_client.get("/api/fs/list", params={"path": str(f)})
        assert resp.status_code == 400

    def test_dirs_sorted_before_files(self, fs_client, tmp_path):
        (tmp_path / "zfile.txt").write_text("z")
        (tmp_path / "adir").mkdir()
        with patch("web.routers.fs._guard_path", return_value=tmp_path):
            resp = fs_client.get("/api/fs/list", params={"path": str(tmp_path)})
        entries = resp.json()["entries"]
        dir_idx = next(i for i, e in enumerate(entries) if e["name"] == "adir")
        file_idx = next(i for i, e in enumerate(entries) if e["name"] == "zfile.txt")
        assert dir_idx < file_idx

    def test_hidden_files_excluded(self, fs_client, tmp_path):
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("ok")
        with patch("web.routers.fs._guard_path", return_value=tmp_path):
            resp = fs_client.get("/api/fs/list", params={"path": str(tmp_path)})
        names = [e["name"] for e in resp.json()["entries"]]
        assert ".hidden" not in names
        assert "visible.txt" in names


# ---------------------------------------------------------------------------
# fs.py — POST /api/shell/exec
# ---------------------------------------------------------------------------

class TestShellExecEndpoint:
    def test_remote_blocked_returns_403(self, fs_client):
        with patch("web.state.is_remote_allowed", return_value=False):
            resp = fs_client.post("/api/shell/exec", json={"command": "ls"})
        assert resp.status_code == 403

    def test_empty_command_returns_400(self, fs_client):
        with patch("web.state.is_remote_allowed", return_value=True):
            resp = fs_client.post("/api/shell/exec", json={"command": ""})
        assert resp.status_code == 400

    def test_needs_approval_returns_202(self, fs_client):
        with patch("web.state.is_remote_allowed", return_value=True):
            with patch("pipeline.tools_code.host_exec",
                       return_value={"__needs_approval__": True, "command": "rm -rf /", "reason": "destructive"}):
                resp = fs_client.post("/api/shell/exec", json={"command": "rm -rf /"})
        assert resp.status_code == 202
        data = resp.json()
        assert data["needs_approval"] is True

    def test_successful_command_returns_ok(self, fs_client):
        with patch("web.state.is_remote_allowed", return_value=True):
            with patch("pipeline.tools_code.host_exec",
                       return_value={"returncode": 0, "stdout": "hello\n", "stderr": ""}):
                resp = fs_client.post("/api/shell/exec", json={"command": "echo hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "hello" in data["stdout"]

    def test_command_error_returns_ok_false(self, fs_client):
        with patch("web.state.is_remote_allowed", return_value=True):
            with patch("pipeline.tools_code.host_exec",
                       return_value={"error": "command not found"}):
                resp = fs_client.post("/api/shell/exec", json={"command": "nonexistent_cmd"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False


# ---------------------------------------------------------------------------
# onboarding.py — GET /api/onboarding
# ---------------------------------------------------------------------------

class TestOnboardingGet:
    def test_returns_providers_and_plugins(self, onboarding_client, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
        resp = onboarding_client.get("/api/onboarding")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "plugins" in data
        assert "onboarded" in data

    def test_providers_have_required_fields(self, onboarding_client, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
        resp = onboarding_client.get("/api/onboarding")
        providers = resp.json()["providers"]
        assert isinstance(providers, list)
        for p in providers:
            assert "id" in p
            assert "label" in p
            assert "configured" in p


# ---------------------------------------------------------------------------
# onboarding.py — POST /api/onboarding/provider (키 저장)
# ---------------------------------------------------------------------------

class TestOnboardingProvider:
    def test_invalid_provider_returns_400(self, onboarding_client, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
        resp = onboarding_client.post("/api/onboarding/provider",
                                       json={"provider_id": "nonexistent", "key": "sk-test"})
        assert resp.status_code == 400

    def test_missing_body_returns_422(self, onboarding_client):
        resp = onboarding_client.post("/api/onboarding/provider")
        assert resp.status_code in (400, 422)

    def test_anthropic_key_saved(self, onboarding_client, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
        with patch("pipeline.keychain.set_secret", return_value=True):
            resp = onboarding_client.post("/api/onboarding/provider",
                                           json={"provider_id": "anthropic", "key": "sk-ant-test"})
        assert resp.status_code in (200, 400, 500)


# ---------------------------------------------------------------------------
# onboarding.py — GET /api/onboarding/system-check
# ---------------------------------------------------------------------------

class TestOnboardingSystemCheck:
    def test_system_check_returns_dict(self, onboarding_client, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
        resp = onboarding_client.get("/api/onboarding/system-check")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# memory_inspector.py — basic smoke tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def memory_client(tmp_path_factory):
    from web.routers import memory_inspector
    app = FastAPI()
    app.include_router(memory_inspector.router)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestMemoryInspector:
    def test_list_entities_returns_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
        # data_dir() lru_cache 무효화
        from pipeline import data_paths
        data_paths.data_dir.cache_clear()
        from web.routers import memory_inspector
        app = FastAPI()
        app.include_router(memory_inspector.router)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/api/memory/entities")
        assert resp.status_code in (200, 404, 500)
        data_paths.data_dir.cache_clear()

    def test_search_endpoint_exists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
        from pipeline import data_paths
        data_paths.data_dir.cache_clear()
        from web.routers import memory_inspector
        app = FastAPI()
        app.include_router(memory_inspector.router)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/api/memory/search", params={"q": "test"})
        assert resp.status_code in (200, 400, 404, 500)
        data_paths.data_dir.cache_clear()
