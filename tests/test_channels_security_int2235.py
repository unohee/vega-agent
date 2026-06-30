# Created: 2026-07-01
# Purpose: 채널 보안·정합성 회귀 (INT-2235 audit) — CE 게이트·메시지 분할·telegram reply 필터.

from __future__ import annotations

import json
from types import SimpleNamespace as NS


def test_split_for_channel():
    from pipeline.channels.core import split_for_channel
    assert split_for_channel("", 10) == [""]
    assert split_for_channel("abc", 10) == ["abc"]
    assert split_for_channel("a" * 25, 10) == ["a" * 10, "a" * 10, "a" * 5]


def test_ce_allowlist_boundary():
    # 로컬 시스템 도구는 제외, SaaS read/write 는 위임 허용
    from pipeline.tools import _CE_ALLOWED_TOOLS
    for blocked in ("host_exec", "bash_exec", "python_exec", "file_edit", "icloud_move"):
        assert blocked not in _CE_ALLOWED_TOOLS
    for allowed in ("gmail_search", "web_search", "ask_user_question"):
        assert allowed in _CE_ALLOWED_TOOLS


def test_ce_gate_blocks_local_tool_in_channel():
    # ce_mode(원격 채널) 에서 host_exec 같은 로컬 도구는 dispatch 단계에서 차단된다.
    from pipeline.tools import dispatch_tool, set_ce_mode
    set_ce_mode(True)
    try:
        r = json.loads(dispatch_tool("host_exec", {"command": "echo hi"}))
        assert r.get("ce_blocked") is True
    finally:
        set_ce_mode(False)


def test_ce_gate_off_for_local_session():
    # ce_mode 가 꺼진 로컬 세션은 게이트 영향 없음 — host_exec 가 ce_blocked 로 막히지 않는다.
    # (실제 실행되므로 ce_blocked 키가 없음을 확인. 실행 결과/에러는 무관.)
    from pipeline.tools import dispatch_tool, set_ce_mode
    set_ce_mode(False)
    r = json.loads(dispatch_tool("host_exec", {"command": "echo int2235"}))
    assert r.get("ce_blocked") is not True


def _mk_update(chat_type: str, text: str, reply_bot: tuple | None):
    reply = None
    if reply_bot is not None:
        is_bot, username = reply_bot
        reply = NS(from_user=NS(is_bot=is_bot, username=username))
    return NS(effective_chat=NS(type=chat_type),
              message=NS(text=text, reply_to_message=reply))


def test_telegram_group_reply_only_own_bot():
    from pipeline.channels.telegram_bot import _should_handle
    # 다른 봇 답글 → 무시
    assert _should_handle(_mk_update("group", "hi", (True, "otherbot")), "mybot") is False
    # 이 봇 답글 → 처리
    assert _should_handle(_mk_update("group", "hi", (True, "mybot")), "mybot") is True
    # 사람 답글 → 무시(멘션 없음)
    assert _should_handle(_mk_update("group", "hi", (False, None)), "mybot") is False
    # DM → 항상 처리
    assert _should_handle(_mk_update("private", "hi", None), "mybot") is True
    # 그룹 멘션 → 처리
    assert _should_handle(_mk_update("group", "hey @mybot", None), "mybot") is True
