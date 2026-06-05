# Created: 2026-06-06
# Purpose: /goal 멀티턴 토큰 최적화 검증. (A) goal 프로토콜을 매 턴 user 메시지로
#          재전송하지 않고 system에 1회 주입 (B) assistant 응답의 도구 진행
#          내레이션을 history/DB에서 슬림화해 self-echo + 토큰 누적을 막는다.
# Dependencies: web.server
# Test Status: 검증 중

from __future__ import annotations

import importlib

import pytest

server = importlib.import_module("web.server")


# ── (B) 슬림화 함수 ──────────────────────────────────────────────

def test_slim_removes_tool_narration():
    """도구 완료/명령 에코 라인(- ✓, - ✗)을 제거하고 설명은 유지."""
    text = (
        "- ✗ \n- ✓ 완료\n- ✓ browser_evaluate 완료\n"
        "- ✓ cd /Users/x/repo && find . -name '*.py'\n\n"
        "ALV 탭이 살아있는지 확인하고 export를 이어갈게."
    )
    out = server._slim_assistant_content(text)
    assert "✓ 완료" not in out
    assert "browser_evaluate" not in out
    assert "find ." not in out
    assert "ALV 탭이 살아있는지" in out  # 설명 텍스트 보존


def test_slim_preserves_plain_text():
    """도구 마커가 없는 순수 텍스트는 그대로 둔다."""
    plain = "이건 일반 답변이야.\n두 번째 줄도 그대로."
    assert server._slim_assistant_content(plain) == plain


def test_slim_handles_empty_and_marker_only():
    """빈 문자열·마커만 있는 경우에도 깨지지 않는다."""
    assert server._slim_assistant_content("") == ""
    # 전부 도구 라인이면 원본을 보존(빈 응답으로 만들지 않음 — 정보 손실 방지)
    only = "- ✓ 완료\n- ✗ "
    out = server._slim_assistant_content(only)
    assert out == only  # 슬림 결과가 비면 원본 유지


def test_slim_keeps_normal_bullets():
    """일반 불릿(도구 마커 없는)은 유지한다 — 마커 라인만 노린다."""
    text = "정리하면:\n- 첫째 항목\n- 둘째 항목\n- ✓ 완료"
    out = server._slim_assistant_content(text)
    assert "첫째 항목" in out and "둘째 항목" in out
    assert "✓ 완료" not in out


def test_slim_various_markers():
    """✓ 외 ✗·⟳·⏹ 마커 라인도 제거."""
    text = "- ⟳ 실행 중\n- ⏹ 중단됨\n남은 설명"
    out = server._slim_assistant_content(text)
    assert "실행 중" not in out and "중단됨" not in out
    assert "남은 설명" in out


# ── (A) goal 프로토콜 system 주입 ────────────────────────────────

def test_goal_guide_is_concise():
    """goal system 가이드가 존재하고 과하게 길지 않다(매 세션 1회 주입되므로)."""
    assert hasattr(server, "_GOAL_MODE_GUIDE")
    assert "되풀이하지 않는다" in server._GOAL_MODE_GUIDE  # self-echo 억제 지시 포함
    # 압축본이어야 — 원래 goal.md 전문(~1800자)보다 짧게
    assert len(server._GOAL_MODE_GUIDE) < 1000, "goal 가이드가 너무 길다(재입력 최적화 의미 약화)"


def test_goal_guide_has_no_repeat_instruction():
    """핵심 의도: 직전에 한 말을 반복하지 말라는 지시가 명시돼야."""
    g = server._GOAL_MODE_GUIDE
    assert "반복하지" in g or "되풀이하지" in g or "재설명 금지" in g
