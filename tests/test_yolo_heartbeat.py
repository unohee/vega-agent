# Created: 2026-06-06
# Purpose: YOLO 하트비트의 '멈춘 세션' 판정(_is_stalled_yolo) 검증. 자동 재개 대상이
#          정확히 'YOLO 모드 + 미완료 + 무활동 임계 초과 + 승인대기 아님 + 상한 미만'일 때만
#          잡히는지, self-wake/정상 세션을 잘못 깨우지 않는지 확인.
# Dependencies: web.server
# Test Status: 검증 중

from __future__ import annotations

import importlib

import pytest

server = importlib.import_module("web.server")

SID = "hb-test-sid"
STALL = server.HEARTBEAT_STALL_SEC


@pytest.fixture(autouse=True)
def _clean():
    server._YOLO_MODE.pop(SID, None)
    server._heartbeat_resumes.pop(SID, None)
    yield
    server._YOLO_MODE.pop(SID, None)
    server._heartbeat_resumes.pop(SID, None)


def _reg(idle_sec: float, done=False, awaiting=False) -> dict:
    """last_activity가 idle_sec초 전인 가짜 reg."""
    import time
    return {
        "done": done,
        "awaiting_approval": awaiting,
        "last_activity": time.monotonic() - idle_sec,
    }


def _now():
    import time
    return time.monotonic()


def test_stalled_yolo_is_detected():
    """YOLO + 미완료 + 무활동 임계 초과 → 재개 대상."""
    server._YOLO_MODE[SID] = True
    assert server._is_stalled_yolo(SID, _reg(STALL + 30), _now()) is True


def test_not_yolo_is_ignored():
    """YOLO 모드가 아니면 아무리 멈춰도 대상 아님."""
    # _YOLO_MODE 미설정
    assert server._is_stalled_yolo(SID, _reg(STALL + 999), _now()) is False


def test_done_is_ignored():
    """완료된 세션은 대상 아님."""
    server._YOLO_MODE[SID] = True
    assert server._is_stalled_yolo(SID, _reg(STALL + 30, done=True), _now()) is False


def test_awaiting_approval_is_ignored():
    """승인 대기는 정상 — 멈춤 아님."""
    server._YOLO_MODE[SID] = True
    assert server._is_stalled_yolo(SID, _reg(STALL + 30, awaiting=True), _now()) is False


def test_recent_activity_is_ignored():
    """무활동이 임계 미만이면(self-wake가 막 갱신한 경우 등) 대상 아님."""
    server._YOLO_MODE[SID] = True
    assert server._is_stalled_yolo(SID, _reg(STALL - 30), _now()) is False


def test_resume_cap_stops_detection():
    """재개 상한에 도달하면 더는 대상 아님 — 무한 재개 방지."""
    server._YOLO_MODE[SID] = True
    server._heartbeat_resumes[SID] = server.HEARTBEAT_MAX_RESUMES
    assert server._is_stalled_yolo(SID, _reg(STALL + 30), _now()) is False
    # 상한 직전(1회 남음)이면 여전히 대상
    server._heartbeat_resumes[SID] = server.HEARTBEAT_MAX_RESUMES - 1
    assert server._is_stalled_yolo(SID, _reg(STALL + 30), _now()) is True


def test_config_sane():
    """하트비트 설정값이 합리적 범위 — 스캔 주기 < 무활동 임계."""
    assert server.HEARTBEAT_INTERVAL < server.HEARTBEAT_STALL_SEC, \
        "스캔 주기가 무활동 임계보다 길면 멈춤을 늦게 잡는다"
    assert server.HEARTBEAT_MAX_RESUMES >= 1
