# Created: 2026-06-23
# Purpose: 호스트 우선 실행(Docker opt-in) + App Support 워크스페이스 누적 카탈로그 회귀 (INT-1870 Phase A).
# Dependencies: pipeline.sandbox, pipeline.tools_code, pipeline.data_paths
# Test Status: green (2026-06-23)

from __future__ import annotations

import pipeline.sandbox as sb
from pipeline.data_paths import workspace_dir
from pipeline.tools_code import (
    _sandboxed_list_skills,
    _sandboxed_save_module,
    python_exec,
)


def test_docker_opt_in_default_off(monkeypatch):
    """기본(VEGA_USE_DOCKER 미설정)은 Docker opt-out — 라우팅이 호스트로 간다(INT-1870 Phase A.1)."""
    monkeypatch.delenv("VEGA_USE_DOCKER", raising=False)
    assert sb.docker_opt_in() is False
    # enabled = opt_in AND available → opt_in False 면 Docker 설치 여부와 무관하게 False
    assert sb.docker_enabled() is False


def test_docker_opt_in_env(monkeypatch):
    """VEGA_USE_DOCKER 로만 Docker 활성 — 값 파싱 확인."""
    monkeypatch.setenv("VEGA_USE_DOCKER", "1")
    assert sb.docker_opt_in() is True
    monkeypatch.setenv("VEGA_USE_DOCKER", "false")
    assert sb.docker_opt_in() is False
    monkeypatch.setenv("VEGA_USE_DOCKER", "on")
    assert sb.docker_opt_in() is True


def test_workspace_catalog_accumulates(monkeypatch, tmp_path):
    """자작 모듈이 App Support 워크스페이스에 영속·카탈로그 기록되고, 다음 실행에서 import 재사용된다.
    누적 카탈로그의 핵심(INT-1870 §4b) — 중복 도구 양산 방지."""
    monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VEGA_USE_DOCKER", raising=False)

    r1 = _sandboxed_save_module("greeter", '"""인사 모듈"""\ndef hi(n):\n    return f"hi {n}"\n')
    assert r1.get("ok"), r1
    ws = workspace_dir()
    assert (ws / "skills" / "greeter.py").exists()
    assert "greeter" in (ws / "CATALOG.md").read_text(encoding="utf-8")

    r2 = _sandboxed_list_skills()
    assert "greeter" in r2.get("skills", []), r2

    # 다음 실행에서 import 재사용 (워크스페이스 skills/ 가 PYTHONPATH 에 있어야 함)
    r3 = python_exec('from greeter import hi; print(hi("VEGA"))')
    assert r3.get("returncode") == 0, r3
    assert "hi VEGA" in r3.get("stdout", ""), r3


def test_exec_cwd_is_workspace(monkeypatch, tmp_path):
    """코드 실행 기본 CWD 가 App Support 워크스페이스 — VEGA 산출물이 home 에 안 흩어진다."""
    monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
    r = python_exec("import os; print(os.getcwd())")
    assert r.get("stdout", "").strip().endswith("workspace"), r
