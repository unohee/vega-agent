# Created: 2026-06-06
# Purpose: YOLO 전역 지속(_YOLO_GLOBAL / _yolo_on) 검증. 한 세션에서 켜면 모든 세션에서
#          자동승인이 유지되는지(데스크탑 1대=유저 1명). 입력창 칩으로 켠 YOLO가 다른
#          세션/새 채팅에서도 적용돼 'YOLO인데 승인 카드 뜸' 버그가 안 나는지.
# Dependencies: web.server
# Test Status: 검증 중

from __future__ import annotations

import importlib

import pytest

server = importlib.import_module("web.server")


@pytest.fixture(autouse=True)
def _reset():
    server._YOLO_GLOBAL = False
    server._YOLO_MODE.clear()
    yield
    server._YOLO_GLOBAL = False
    server._YOLO_MODE.clear()


def test_global_off_by_default():
    assert server._yolo_on("any-sid") is False


def test_global_on_applies_to_all_sessions():
    """전역을 켜면 어떤 sid에서도 YOLO가 켜진 것으로 본다 — sid 불일치 버그 방지."""
    server._YOLO_GLOBAL = True
    assert server._yolo_on("session-A") is True
    assert server._yolo_on("session-B") is True
    assert server._yolo_on("never-seen-sid") is True


def test_session_local_still_works():
    """하위호환 — 세션별 _YOLO_MODE도 여전히 인정."""
    server._YOLO_MODE["only-this"] = True
    assert server._yolo_on("only-this") is True
    assert server._yolo_on("other") is False


def test_global_or_local():
    """전역 OR 세션별 — 둘 중 하나만 켜져도 on."""
    server._YOLO_GLOBAL = True
    server._YOLO_MODE.clear()
    assert server._yolo_on("x") is True
    server._YOLO_GLOBAL = False
    server._YOLO_MODE["x"] = True
    assert server._yolo_on("x") is True


def test_slash_yolo_sets_global():
    """/yolo가 전역을 켠다."""
    server.handle_slash("/yolo", "sid-1")
    assert server._YOLO_GLOBAL is True
    # 다른 세션에서도 적용
    assert server._yolo_on("sid-2") is True


def test_slash_yolo_off_clears_global_and_local():
    """/yolo-off가 전역 + 세션별 잔재를 모두 끈다."""
    server._YOLO_GLOBAL = True
    server._YOLO_MODE["leftover"] = True
    server.handle_slash("/yolo-off", "sid-1")
    assert server._YOLO_GLOBAL is False
    assert server._yolo_on("leftover") is False
    assert len(server._YOLO_MODE) == 0
