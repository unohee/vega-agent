# Created: 2026-06-11
# Purpose: Docker 미설치/데몬 미기동 시 샌드박스 graceful degradation 회귀 테스트 (INT-1459)
#          — raw FileNotFoundError("[Errno 2] ... 'docker'")가 도구 결과로 노출되면 안 됨.
# Dependencies: pipeline/sandbox.py
# Test Status: green (2026-06-11)

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import pipeline.sandbox as sb

_IS_WIN = sys.platform == "win32"


@pytest.fixture(autouse=True)
def _reset_ok_cache():
    """docker_state()의 'ok' 캐시를 테스트 간 격리."""
    sb._docker_ok_until = 0.0
    yield
    sb._docker_ok_until = 0.0


def _strip_docker_from_path(monkeypatch, tmp_path):
    """PATH를 빈 디렉터리로 바꿔 docker 바이너리가 없는 환경을 시뮬레이션."""
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


def _make_fake_docker(directory: Path, exit_code: int = 1) -> Path:
    """shutil.which가 찾을 수 있는 fake docker 바이너리를 생성한다.

    Windows: docker.exe (내용 무관 — subprocess.run은 mock으로 대체).
    Unix: 실행 가능한 쉘 스크립트.
    """
    if _IS_WIN:
        fake = directory / "docker.exe"
        fake.write_bytes(b"")  # 내용 무관 — which가 찾기만 하면 됨
    else:
        fake = directory / "docker"
        fake.write_text(f"#!/bin/sh\nexit {exit_code}\n")
        fake.chmod(0o755)
    return fake


class TestDockerState:
    def test_missing_when_not_on_path(self, monkeypatch, tmp_path):
        """PATH에 docker 없음 → 'missing' (shutil.which 기반, 매 호출 재평가)."""
        _strip_docker_from_path(monkeypatch, tmp_path)
        assert sb.docker_state() == "missing"
        assert sb.docker_available() is False

    def test_down_when_daemon_not_responding(self, monkeypatch, tmp_path):
        """바이너리는 있는데 데몬 미기동(docker info 실패) → 'down'."""
        fake = _make_fake_docker(tmp_path, exit_code=1)
        monkeypatch.setenv("PATH", str(tmp_path))
        if _IS_WIN:
            # Windows: shutil.which가 tmp_path의 .exe를 못 찾을 수 있어 직접 patch
            mock_r = MagicMock()
            mock_r.returncode = 1
            with patch.object(sb.shutil, "which", return_value=str(fake)), \
                 patch.object(sb.subprocess, "run", return_value=mock_r):
                assert sb.docker_state() == "down"
                assert sb.docker_available() is False
        else:
            assert sb.docker_state() == "down"
            assert sb.docker_available() is False

    def test_ok_cached_but_negative_not_cached(self, monkeypatch, tmp_path):
        """'ok'만 TTL 캐시 — 부정 판정은 캐시하지 않아 설치 후 재시도가 즉시 감지된다."""
        # 1) missing 상태 (캐시 안 됨)
        _strip_docker_from_path(monkeypatch, tmp_path)
        assert sb.docker_state() == "missing"
        # 2) 사용자가 docker를 '설치'함 → 다음 호출에서 바로 ok
        fake = _make_fake_docker(tmp_path / "emptybin", exit_code=0)
        if _IS_WIN:
            mock_r = MagicMock()
            mock_r.returncode = 0
            with patch.object(sb.shutil, "which", return_value=str(fake)), \
                 patch.object(sb.subprocess, "run", return_value=mock_r):
                assert sb.docker_state() == "ok"
        else:
            assert sb.docker_state() == "ok"
        # 3) ok는 캐시됨 — docker info를 다시 부르지 않아도 ok 유지
        with patch.object(sb.subprocess, "run", side_effect=AssertionError("cached여야 함")):
            assert sb.docker_state() == "ok"


class TestGracefulToolResults:
    """sandbox_* 도구는 docker 부재 시 예외 대신 친화 에러 dict를 반환해야 한다."""

    def test_sandbox_bash_missing_docker(self, monkeypatch, tmp_path):
        _strip_docker_from_path(monkeypatch, tmp_path)
        result = sb.sandbox_bash("echo hi")
        assert "error" in result
        assert "[Errno 2]" not in result["error"]
        assert "Docker" in result["error"] and "설치" in result["error"]
        assert result.get("sandbox_disabled") is True
        assert result.get("docker") == "missing"

    def test_sandbox_python_missing_docker(self, monkeypatch, tmp_path):
        _strip_docker_from_path(monkeypatch, tmp_path)
        result = sb.sandbox_python("print(1)")
        assert "Docker" in result.get("error", "")
        assert "orbstack.dev" in result["error"]  # 설치 안내 링크

    def test_sandbox_bash_daemon_down_distinct_message(self, monkeypatch, tmp_path):
        """docker는 있는데 데몬이 죽어 있으면 '설치' 안내가 아니라 '실행' 안내."""
        fake = _make_fake_docker(tmp_path, exit_code=1)
        monkeypatch.setenv("PATH", str(tmp_path))
        if _IS_WIN:
            mock_r = MagicMock()
            mock_r.returncode = 1
            with patch.object(sb.shutil, "which", return_value=str(fake)), \
                 patch.object(sb.subprocess, "run", return_value=mock_r):
                result = sb.sandbox_bash("echo hi")
        else:
            result = sb.sandbox_bash("echo hi")
        assert result.get("docker") == "down"
        assert "데몬" in result["error"] and "실행" in result["error"]
        assert "orbstack.dev" not in result["error"]

    def test_sandbox_pip_install_blocked_before_wheel_download(self, monkeypatch, tmp_path):
        """pip 경로는 호스트 wheel 다운로드 전에 차단돼야 한다 (에러 dict 유실 방지)."""
        _strip_docker_from_path(monkeypatch, tmp_path)
        with patch.object(sb.subprocess, "run", side_effect=AssertionError("호출되면 안 됨")):
            result = sb.sandbox_bash("pip install requests")
        assert result.get("sandbox_disabled") is True

    def test_sandbox_status_missing_docker(self, monkeypatch, tmp_path):
        _strip_docker_from_path(monkeypatch, tmp_path)
        result = sb.sandbox_status()
        assert result["running"] is False
        assert result.get("docker") == "missing"
        assert "Docker" in result.get("error", "")

    def test_project_dir_route_also_guarded(self, monkeypatch, tmp_path):
        """작업 폴더(_exec_project) 경로도 같은 게이트를 탄다."""
        _strip_docker_from_path(monkeypatch, tmp_path)
        token = sb._PROJECT_DIR.set(str(tmp_path))
        try:
            result = sb.sandbox_bash("ls")
        finally:
            sb._PROJECT_DIR.reset(token)
        assert result.get("sandbox_disabled") is True


class TestEnsureSandboxReady:
    def test_reason_distinguishes_missing_vs_down(self, monkeypatch, tmp_path):
        _strip_docker_from_path(monkeypatch, tmp_path)
        assert sb.ensure_sandbox_ready() == {"ready": False, "reason": "docker_missing"}
        fake = _make_fake_docker(tmp_path / "emptybin", exit_code=1)
        if _IS_WIN:
            mock_r = MagicMock()
            mock_r.returncode = 1
            with patch.object(sb.shutil, "which", return_value=str(fake)), \
                 patch.object(sb.subprocess, "run", return_value=mock_r):
                assert sb.ensure_sandbox_ready() == {"ready": False, "reason": "docker_down"}
        else:
            assert sb.ensure_sandbox_ready() == {"ready": False, "reason": "docker_down"}


class TestResolveComposeDir:
    """frozen 앱에서 docker compose 의 cwd(COMPOSE_DIR) 해석 회귀 (INT-1505 __file__ 함정과 동형).

    noarchive=False 빌드에선 sandbox.py 가 PYZ 안이라 __file__ 추정이 빗나가
    `docker compose up` 의 cwd 가 실존하지 않는 경로가 된다 → 샌드박스 기동 실패.
    launcher 가 설정하는 VEGA_BUNDLE_ROOT(=_MEIPASS)/sandbox 를 우선 봐야 한다.
    """

    def test_dev_env_uses_repo_sandbox(self, monkeypatch):
        """VEGA_BUNDLE_ROOT 미설정(개발) → repo 의 sandbox/ (실존)."""
        monkeypatch.delenv("VEGA_BUNDLE_ROOT", raising=False)
        d = sb._resolve_compose_dir()
        assert d.name == "sandbox"
        assert (d / "docker-compose.yml").exists()

    def test_bundle_root_with_compose_selected(self, monkeypatch, tmp_path):
        """VEGA_BUNDLE_ROOT/sandbox 에 compose 가 있으면 그 경로를 쓴다."""
        bundle = tmp_path / "meipass"
        (bundle / "sandbox").mkdir(parents=True)
        (bundle / "sandbox" / "docker-compose.yml").write_text("services: {}\n")
        monkeypatch.setenv("VEGA_BUNDLE_ROOT", str(bundle))
        assert sb._resolve_compose_dir() == bundle / "sandbox"

    def test_bundle_root_without_compose_falls_back(self, monkeypatch, tmp_path):
        """VEGA_BUNDLE_ROOT 가 가리키는 곳에 compose 가 없으면 repo 폴백 (잘못된 경로 반환 금지)."""
        monkeypatch.setenv("VEGA_BUNDLE_ROOT", str(tmp_path / "nope"))
        d = sb._resolve_compose_dir()
        assert "nope" not in str(d)
        assert (d / "docker-compose.yml").exists()
