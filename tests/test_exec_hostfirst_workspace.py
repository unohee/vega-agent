# Created: 2026-06-23
# Purpose: 호스트 실행 + App Support 워크스페이스 누적 카탈로그 회귀 (INT-1870 Phase A/C).
# Dependencies: pipeline.tools_code, pipeline.data_paths
# Test Status: green (2026-06-23)

from __future__ import annotations

from pipeline.data_paths import workspace_dir
from pipeline.tools_code import (
    _sandboxed_list_skills,
    _sandboxed_save_module,
    python_exec,
)

# (docker_opt_in/docker_enabled 테스트 제거 — Docker 자체를 제거함, INT-1870 Phase C.
#  코드 실행은 항상 호스트 동봉 인터프리터로 동작한다.)


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


def test_self_improve_patch_test_runs_on_host(monkeypatch, tmp_path):
    """self_improve 패치 테스트가 Docker 없이 호스트에서 동작 — L6 갭(자기개선 Docker 의존) 해소(INT-1870)."""
    monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VEGA_USE_DOCKER", raising=False)
    from pipeline import self_improve as si
    patch = 'def _vega_probe(x):\n    return {"ok": True, "doubled": x * 2}\n'
    r = si._test_patch("_vega_probe", patch, {"x": 5})
    assert r.get("ok") is True, r
    assert r.get("result", {}).get("doubled") == 10, r
