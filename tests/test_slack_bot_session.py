# Created: 2026-06-29
# Purpose: Slack 봇 세션 키(대화 격리) — DM dementia 회귀 방지 (ST 4215).
#   DM 은 스레드를 거의 안 써 매 메시지 ts 가 바뀌면 새 세션이 되어 직전 대화를
#   기억 못 했다. DM 은 채널 자체를 안정 키로 써야 한다.
# Dependencies: slack_bolt (설치 시에만; 없으면 skip)
# Test Status: passing

from __future__ import annotations

import pytest

slack_bot = pytest.importorskip("pipeline.channels.slack_bot")


def test_dm_session_stable_across_messages():
    """DM: 스레드 없는 순차 메시지(ts 가 매번 다름)도 같은 conv_id → 대화 기억 유지."""
    f = slack_bot._reply_ts_and_conv_id
    _, c1 = f("D0ABC", {"ts": "100.1"})
    _, c2 = f("D0ABC", {"ts": "200.2"})
    _, c3 = f("D0ABC", {"ts": "300.3"})
    assert c1 == c2 == c3 == "D0ABC:dm"


def test_channel_session_keyed_by_thread():
    """채널 멘션: 같은 스레드(thread_ts) = 같은 세션, 새 멘션 = 새 대화."""
    f = slack_bot._reply_ts_and_conv_id
    # 스레드 부모 멘션
    r1, c1 = f("C0XYZ", {"ts": "500.5"})
    # 그 스레드 답글 — thread_ts 가 부모를 가리킴 → 같은 세션
    r2, c2 = f("C0XYZ", {"thread_ts": "500.5", "ts": "501.6"})
    assert c1 == c2 == "C0XYZ:500.5"
    assert r1 == "500.5" and r2 == "500.5"   # 둘 다 부모 스레드에 답글
    # 별개의 새 멘션(다른 ts, thread 없음) → 다른 세션
    _, c3 = f("C0XYZ", {"ts": "600.6"})
    assert c3 != c1


def test_reply_ts_prefers_thread_parent():
    """답글 위치: thread_ts 가 있으면 그 부모, 없으면 이 메시지 ts."""
    f = slack_bot._reply_ts_and_conv_id
    assert f("D0ABC", {"thread_ts": "1.1", "ts": "2.2"})[0] == "1.1"
    assert f("D0ABC", {"ts": "2.2"})[0] == "2.2"
