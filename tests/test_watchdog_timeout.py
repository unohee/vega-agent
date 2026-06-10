# Created: 2026-06-06
# Purpose: hang 워치독의 모드별 동적 임계(_watchdog_idle_for) 검증.
#          일반 60초 / yolo·goal·research 300초. 테스트 유저 hang 체감 개선 +
#          장기작업 모드만 길게 잡는 정책의 회귀 방지.
# Dependencies: web.server
# Test Status: 검증 중

from __future__ import annotations

import importlib

import pytest

server = importlib.import_module("web.server")


@pytest.fixture(autouse=True)
def _clear_modes():
    """각 테스트 전후로 모드 dict를 깨끗이 — 세션 누수 방지.
    _YOLO_GLOBAL은 실 데이터 디렉터리의 플래그 파일에서 로드되므로(라이브 데몬이
    YOLO를 켜두면 True) 반드시 격리한다. import binding 함정 — server 재수출이
    아니라 web.state 모듈 변수에 직접 설정해야 yolo_on이 본다."""
    import web.state as _state
    saved_global = _state._YOLO_GLOBAL
    _state._YOLO_GLOBAL = False
    sid = "test-sid"
    for d in (server._YOLO_MODE, server._GOAL_MODE, server._RESEARCH_MODE):
        d.pop(sid, None)
    yield
    _state._YOLO_GLOBAL = saved_global
    for d in (server._YOLO_MODE, server._GOAL_MODE, server._RESEARCH_MODE):
        d.pop(sid, None)


def test_default_idle_is_60s():
    """모드가 하나도 없으면 일반 임계(60초)."""
    assert server._watchdog_idle_for("test-sid") == server.WATCHDOG_IDLE_DEFAULT == 60.0


def test_yolo_extends_to_300s():
    server._YOLO_MODE["test-sid"] = True
    assert server._watchdog_idle_for("test-sid") == server.WATCHDOG_IDLE_LONG == 300.0


def test_goal_extends_to_300s():
    server._GOAL_MODE["test-sid"] = True
    assert server._watchdog_idle_for("test-sid") == 300.0


def test_research_extends_to_300s():
    server._RESEARCH_MODE["test-sid"] = True
    assert server._watchdog_idle_for("test-sid") == 300.0


def test_long_threshold_strictly_greater_than_default():
    """장기 임계는 항상 일반보다 길어야 — 정책 역전 방지."""
    assert server.WATCHDOG_IDLE_LONG > server.WATCHDOG_IDLE_DEFAULT


def test_modes_are_independent_per_session():
    """한 세션의 모드가 다른 세션 임계에 새지 않는다."""
    server._YOLO_MODE["test-sid"] = True
    assert server._watchdog_idle_for("test-sid") == 300.0
    assert server._watchdog_idle_for("other-sid") == 60.0
