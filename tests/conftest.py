# Created: 2026-06-12
# Purpose: 테스트 공통 픽스처 — memory_settings.json 격리.
#          _needs_compaction 등이 사용자 설정(memory_settings.json)을 핫리로드하므로,
#          개발 머신의 실제 데이터 디렉터리 설정값이 테스트 결과를 바꾸지 않도록
#          존재하지 않는 tmp 경로로 돌려 항상 기본값(_SETTINGS_DEFAULTS)을 보게 한다.
# Dependencies: pipeline/data_paths.py, pipeline/compaction.py
# Test Status: 전체 스위트 green 확인

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

# Registry of per-worker tmpdirs created by pytest_configure so we can clean up.
_WORKER_TMPDIRS: list[str] = []


def pytest_configure(config) -> None:
    """Per-worker DB isolation for pytest-xdist on Windows.

    CI sets VEGA_DATA_DIR to a shared runner-temp path. All xdist workers would
    then import pipeline.session_store → _ensure_schema() → sqlite3.connect() on
    the same file simultaneously during collection, causing OperationalError on
    Windows (stricter file locking than POSIX) and a follow-on
    'Different tests were collected' xdist error.

    Fix: each worker process gets its own tmpdir so DB files never collide.
    pytest_configure runs before any test module is imported, so data_paths.data_dir()
    and session_store.DB_PATH both pick up the per-worker env var on first call.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
    if not worker_id:
        return
    tmpdir = tempfile.mkdtemp(prefix=f"vega_pytest_{worker_id}_")
    _WORKER_TMPDIRS.append(tmpdir)
    os.environ["VEGA_DATA_DIR"] = tmpdir
    os.environ["VEGA_DB_FILE"] = os.path.join(tmpdir, "vega.db")
    # Clear lru_cache on data_dir() in case data_paths was somehow pre-imported.
    try:
        from pipeline.data_paths import data_dir
        data_dir.cache_clear()
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """Remove per-worker tmpdirs created by pytest_configure."""
    for tmpdir in _WORKER_TMPDIRS:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_memory_settings(tmp_path, monkeypatch):
    """memory_settings.json 경로를 테스트별 tmp로 격리 (기본값 적용).

    설정 저장/로드를 직접 검증하는 테스트는 자체 fixture에서 같은 방식으로
    다시 monkeypatch하면 된다 (fixture 레벨 패치가 이 autouse를 덮는다).
    """
    monkeypatch.setattr(
        "pipeline.data_paths.memory_settings_path",
        lambda: tmp_path / "memory_settings.json",
    )
