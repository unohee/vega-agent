# Created: 2026-06-12
# Purpose: 테스트 공통 픽스처 — memory_settings.json 격리.
#          _needs_compaction 등이 사용자 설정(memory_settings.json)을 핫리로드하므로,
#          개발 머신의 실제 데이터 디렉터리 설정값이 테스트 결과를 바꾸지 않도록
#          존재하지 않는 tmp 경로로 돌려 항상 기본값(_SETTINGS_DEFAULTS)을 보게 한다.
# Dependencies: pipeline/data_paths.py, pipeline/compaction.py
# Test Status: 전체 스위트 green 확인

from __future__ import annotations

import pytest


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
