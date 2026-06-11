"""
server.py 라우터 분리 후 회귀 테스트.

각 router 모듈(oauth, upload, stt, sessions, admin)의 엔드포인트가
TestClient를 통해 접근 가능하고 예상 응답 구조를 반환하는지 확인한다.

실제 외부 서비스(Slack, Google, STT API 등)는 mock으로 대체한다.
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── 앱 픽스처 ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """TestClient — lifespan 없이 라우트만 테스트."""
    from fastapi import FastAPI
    from web.routers import oauth, upload, stt, sessions, admin, onboarding

    app = FastAPI()
    app.include_router(oauth.router)
    app.include_router(upload.router)
    app.include_router(stt.router)
    app.include_router(sessions.router)
    app.include_router(admin.router)
    app.include_router(onboarding.router)

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── OAuth 라우터 ──────────────────────────────────────────────────────────────

class TestOAuthRouter:
    def test_slack_auth_redirects(self, client):
        with patch("pipeline.auth.slack.authorize_url", return_value="https://slack.example.com/oauth"):
            resp = client.get("/slack/auth", follow_redirects=False)
        assert resp.status_code == 302
        assert "slack.example.com" in resp.headers["location"]

    def test_slack_auth_error_returns_500(self, client):
        with patch("pipeline.auth.slack.authorize_url", side_effect=RuntimeError("no client")):
            resp = client.get("/slack/auth", follow_redirects=False)
        assert resp.status_code == 500
        assert "Slack OAuth 설정 오류" in resp.text

    def test_slack_callback_missing_code(self, client):
        resp = client.get("/slack/callback")
        assert resp.status_code == 400

    def test_slack_callback_error_param(self, client):
        resp = client.get("/slack/callback?error=access_denied")
        assert resp.status_code == 400
        assert "access_denied" in resp.text

    def test_slack_callback_success(self, client):
        with patch("pipeline.auth.slack.exchange_code", return_value={"ok": True, "team": "VEGA", "user": "tester"}):
            resp = client.get("/slack/callback?code=abc123")
        assert resp.status_code == 200
        assert "VEGA" in resp.text

    def test_superthread_auth_redirects(self, client):
        with patch("pipeline.auth.superthread.authorize_url", return_value="https://st.example.com/oauth"):
            resp = client.get("/superthread/auth", follow_redirects=False)
        assert resp.status_code == 302

    def test_callback_missing_code(self, client):
        resp = client.get("/callback")
        assert resp.status_code == 400

    def test_google_auth_redirects(self, client):
        with patch("pipeline.auth.google.authorize_url", return_value="https://accounts.google.com/o/oauth2/auth"):
            resp = client.get("/google/auth", follow_redirects=False)
        assert resp.status_code == 302

    def test_google_callback_missing_code(self, client):
        resp = client.get("/google/callback")
        assert resp.status_code == 400

    def test_google_callback_success(self, client):
        with patch("pipeline.auth.google.exchange_code", return_value={"ok": True, "email": "test@gmail.com"}):
            resp = client.get("/google/callback?code=xyz")
        assert resp.status_code == 200
        assert "test@gmail.com" in resp.text


# ── Upload 라우터 ─────────────────────────────────────────────────────────────

class TestUploadRouter:
    def test_upload_file_no_field(self, client):
        resp = client.post("/api/upload", data={})
        assert resp.status_code == 400
        assert "file" in resp.json()["error"]

    def test_upload_file_too_large(self, client):
        big = b"x" * (101 * 1024 * 1024)
        resp = client.post("/api/upload", files={"file": ("big.txt", io.BytesIO(big), "text/plain")})
        assert resp.status_code == 413

    def test_upload_file_success(self, client, tmp_path, monkeypatch):
        from web.routers import upload as _up
        monkeypatch.setattr(_up, "_UPLOAD_DIR", tmp_path)
        content = b"hello world"
        resp = client.post("/api/upload", files={"file": ("test.txt", io.BytesIO(content), "text/plain")})
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "test.txt"
        assert Path(data["path"]).exists()

    def test_upload_image_no_data(self, client):
        resp = client.post("/api/upload/image", json={"data": "", "media_type": "image/png"})
        assert resp.status_code == 400

    def test_upload_image_success(self, client, tmp_path, monkeypatch):
        from web.routers import upload as _up
        monkeypatch.setattr(_up, "_UPLOAD_DIR", tmp_path)
        png_1x1 = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
            b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        b64 = base64.b64encode(png_1x1).decode()
        resp = client.post("/api/upload/image", json={"data": b64, "media_type": "image/png", "name": "dot.png"})
        assert resp.status_code == 200
        assert Path(resp.json()["path"]).exists()


# ── STT 라우터 ────────────────────────────────────────────────────────────────

class TestSTTRouter:
    def test_stt_no_file(self, client):
        resp = client.post("/api/stt", data={})
        assert resp.status_code == 400

    def test_stt_too_large(self, client):
        big = b"x" * (26 * 1024 * 1024)
        resp = client.post("/api/stt", files={"file": ("audio.webm", io.BytesIO(big), "audio/webm")})
        assert resp.status_code == 413

    def test_stt_success(self, client):
        audio = b"\x00" * 1024
        with patch("pipeline.stt_gateway.transcribe", return_value="안녕하세요"):
            resp = client.post("/api/stt", files={"file": ("audio.webm", io.BytesIO(audio), "audio/webm")})
        assert resp.status_code == 200
        assert resp.json()["text"] == "안녕하세요"

    def test_stt_local_unavailable(self, client):
        from pipeline.stt_gateway import LocalSTTUnavailable
        audio = b"\x00" * 1024
        with patch("pipeline.stt_gateway.transcribe", side_effect=LocalSTTUnavailable("no whisper")):
            resp = client.post("/api/stt", files={"file": ("audio.webm", io.BytesIO(audio), "audio/webm")})
        assert resp.status_code == 503
        assert resp.json()["code"] == "local_stt_unavailable"

    def test_stt_get_config(self, client):
        with patch("pipeline.stt_gateway.get_stt_config", return_value={"provider": "openai"}):
            resp = client.get("/api/stt/config")
        assert resp.status_code == 200
        assert "provider" in resp.json()

    def test_stt_set_config(self, client):
        with patch("pipeline.stt_gateway.set_stt_config") as mock_set:
            resp = client.post("/api/stt/config", json={"provider": "local", "model": "whisper"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ── Sessions 라우터 ───────────────────────────────────────────────────────────

class TestSessionsRouter:
    def test_sessions_list(self, client):
        with patch("pipeline.session_store.list_sessions", return_value=[{"uuid": "abc", "name": "test"}]):
            resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert isinstance(resp.json()["sessions"], list)

    def test_session_create(self, client):
        with patch("pipeline.session_store.create_session", return_value="new-sid"):
            resp = client.post("/api/sessions", json={"title": "테스트"})
        assert resp.status_code == 200
        assert resp.json()["uuid"] == "new-sid"

    def test_session_rename(self, client):
        with patch("pipeline.session_store.rename_session") as mock_rename:
            resp = client.put("/api/sessions/test-sid/rename", json={"name": "새 이름"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_session_delete(self, client):
        with patch("pipeline.session_store.delete_session"):
            resp = client.delete("/api/sessions/test-sid")
        assert resp.status_code == 200

    def test_get_plan_mode_default_false(self, client):
        resp = client.get("/api/sessions/new-sid-xyz/plan-mode")
        assert resp.status_code == 200
        assert resp.json()["plan_mode"] is False

    def test_set_plan_mode_on(self, client):
        resp = client.post("/api/sessions/test-sid/plan-mode", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["plan_mode"] is True

    def test_set_plan_mode_off(self, client):
        resp = client.post("/api/sessions/test-sid/plan-mode", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["plan_mode"] is False

    def test_get_research_mode_default_false(self, client):
        resp = client.get("/api/sessions/new-sid-xyz/research-mode")
        assert resp.status_code == 200
        assert resp.json()["research_mode"] is False

    def test_get_yolo_mode(self, client):
        resp = client.get("/api/sessions/any-sid/yolo-mode")
        assert resp.status_code == 200
        assert "yolo_mode" in resp.json()

    # ── permission-mode (INT-1452) — chat.html 통합 토글 계약 ──

    def test_permission_mode_default(self, client):
        resp = client.get("/api/sessions/perm-sid-fresh/permission-mode")
        assert resp.status_code == 200
        assert resp.json()["permission_mode"] == "default"

    def test_permission_mode_set_bypass_persists(self, client):
        """버그 재현 케이스: bypass 설정 후 GET 이 default 로 풀리면 안 된다."""
        resp = client.post("/api/sessions/perm-sid-a/permission-mode", json={"mode": "bypass"})
        assert resp.status_code == 200
        assert resp.json()["permission_mode"] == "bypass"
        resp = client.get("/api/sessions/perm-sid-a/permission-mode")
        assert resp.json()["permission_mode"] == "bypass"
        # 백엔드 소비처와 같은 상태를 공유하는지
        from web.state import yolo_on
        assert yolo_on("perm-sid-a") is True
        # 정리
        client.post("/api/sessions/perm-sid-a/permission-mode", json={"mode": "default"})

    def test_permission_mode_plan_bypass_exclusive(self, client):
        from web.state import _PLAN_MODE, _YOLO_MODE
        client.post("/api/sessions/perm-sid-b/permission-mode", json={"mode": "plan"})
        assert _PLAN_MODE.get("perm-sid-b") is True
        client.post("/api/sessions/perm-sid-b/permission-mode", json={"mode": "bypass"})
        assert not _PLAN_MODE.get("perm-sid-b")
        assert _YOLO_MODE.get("perm-sid-b") is True
        client.post("/api/sessions/perm-sid-b/permission-mode", json={"mode": "default"})
        assert not _PLAN_MODE.get("perm-sid-b") and not _YOLO_MODE.get("perm-sid-b")

    def test_permission_mode_cycle(self, client):
        """default → plan → bypass → default 순환 (프론트 {cycle:true} 계약)."""
        sid = "perm-sid-cycle"
        seen = []
        for _ in range(3):
            resp = client.post(f"/api/sessions/{sid}/permission-mode", json={"cycle": True})
            seen.append(resp.json()["permission_mode"])
        assert seen == ["plan", "bypass", "default"]

    def test_permission_mode_unknown_rejected(self, client):
        resp = client.post("/api/sessions/perm-sid-c/permission-mode", json={"mode": "yolo"})
        assert resp.status_code == 400

    def test_system_check_docker_available(self, client):
        """INT-1453: Docker 가용 시 hint 없이 available=True."""
        with patch("pipeline.sandbox.docker_available", return_value=True):
            resp = client.get("/api/onboarding/system-check")
        assert resp.status_code == 200
        d = resp.json()["docker"]
        assert d["available"] is True
        assert d["hint"] is None

    def test_system_check_docker_missing(self, client):
        """INT-1453: Docker 미설치 시 비차단 안내(hint+install_url)를 내려준다."""
        with patch("pipeline.sandbox.docker_available", return_value=False):
            resp = client.get("/api/onboarding/system-check")
        assert resp.status_code == 200
        d = resp.json()["docker"]
        assert d["available"] is False
        assert "코드 실행" in d["hint"]
        assert d["install_url"].startswith("https://")

    def test_permission_mode_default_clears_global_yolo(self, client):
        """전역 YOLO 가 켜진 상태에서 default 로 내리면 전역 플래그도 꺼져야 한다."""
        import web.state as _state
        prev = _state._YOLO_GLOBAL
        try:
            with patch("web.routers.sessions.save_yolo_global"):
                _state._YOLO_GLOBAL = True
                resp = client.post("/api/sessions/perm-sid-d/permission-mode", json={"mode": "default"})
                assert resp.json()["permission_mode"] == "default"
                assert _state._YOLO_GLOBAL is False
                resp = client.get("/api/sessions/perm-sid-d/permission-mode")
                assert resp.json()["permission_mode"] == "default"
        finally:
            _state._YOLO_GLOBAL = prev

    def test_active_sessions_empty(self, client):
        resp = client.get("/api/sessions/active")
        assert resp.status_code == 200
        assert isinstance(resp.json()["active"], list)

    def test_session_workdir_invalid_path(self, client):
        resp = client.post("/api/sessions/test-sid/workdir", json={"path": "/nonexistent/path/xyz"})
        assert resp.status_code == 400

    def test_session_workdir_null(self, client):
        with patch("pipeline.session_store.set_working_dir"):
            resp = client.post("/api/sessions/test-sid/workdir", json={"path": None})
        assert resp.status_code == 200

    def test_session_history_not_found(self, client):
        with patch("pipeline.session_store.get_session", return_value=None):
            resp = client.get("/api/sessions/nonexistent/history")
        assert resp.status_code == 404

    def test_session_history_found(self, client):
        with patch("pipeline.session_store.get_session", return_value={"name": "Test"}):
            with patch("pipeline.session_store.load_history_with_meta", return_value=[]):
                resp = client.get("/api/sessions/test-sid/history")
        assert resp.status_code == 200
        assert "messages" in resp.json()

    def test_autopilot_off(self, client):
        with patch("web.state.autopilot_unregister"):
            resp = client.post("/api/sessions/test-sid/autopilot-off")
        assert resp.status_code == 200
        assert resp.json()["autopilot"] is False


# ── Admin 라우터 ──────────────────────────────────────────────────────────────
# TestClient의 request.client가 None이므로 is_loopback을 True로 패치해서 테스트
# (loopback 판정은 web.state.is_loopback 단일 출처 — admin이 import한 이름을 patch)

class TestAdminRouter:
    def test_keys_list_local(self, client):
        with patch("web.routers.admin.is_loopback", return_value=True), \
             patch("web.routers.admin.load_enterprise_keys", return_value=frozenset(["vk_abc", "vk_def"])):
            resp = client.get("/api/admin/keys")
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data
        assert data["count"] == 2

    def test_keys_add_valid(self, client):
        with patch("web.routers.admin.is_loopback", return_value=True), \
             patch("pipeline.keychain.get_secret", return_value=""), \
             patch("pipeline.keychain.set_secret"):
            resp = client.post("/api/admin/keys", json={"key": "vk_newkey"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_keys_add_invalid_prefix(self, client):
        with patch("web.routers.admin.is_loopback", return_value=True):
            resp = client.post("/api/admin/keys", json={"key": "invalid_key"})
        assert resp.status_code == 400

    def test_keys_add_missing_key(self, client):
        with patch("web.routers.admin.is_loopback", return_value=True):
            resp = client.post("/api/admin/keys", json={})
        assert resp.status_code == 400

    def test_keys_delete(self, client):
        with patch("web.routers.admin.is_loopback", return_value=True), \
             patch("pipeline.keychain.get_secret", return_value="vk_abc,vk_def"), \
             patch("pipeline.keychain.set_secret"):
            resp = client.delete("/api/admin/keys/vk_abc")
        assert resp.status_code == 200
        assert "remaining" in resp.json()

    def test_keys_remote_blocked(self, client):
        """원격 접속은 403으로 차단돼야 한다."""
        with patch("web.routers.admin.is_loopback", return_value=False):
            resp = client.get("/api/admin/keys")
        assert resp.status_code == 403
