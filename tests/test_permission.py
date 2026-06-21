# Created: 2026-06-08
# Purpose: pipeline/permission.py 단위 테스트 — 권한 레벨 분류·consent 정책 (INT-1386)
# Dependencies: pipeline/permission.py

from __future__ import annotations

from pipeline.permission import (
    Level,
    get_level,
    requires_consent,
    level_meta,
)


class TestGetLevel:
    def test_read_tools(self):
        for t in ("web_search", "gmail_search", "file_read", "memory_recall"):
            assert get_level(t) == Level.READ, t

    def test_write_tools(self):
        for t in ("gmail_draft", "file_edit", "calendar_create_event"):
            assert get_level(t) == Level.WRITE, t

    def test_delete_tools(self):
        for t in ("file_delete", "skill_delete", "memory_persona_delete", "calendar_delete_event"):
            assert get_level(t) == Level.DELETE, t

    def test_order_tools(self):
        for t in ("kis_order_execute", "kis_order_cancel", "kis_order_modify"):
            assert get_level(t) == Level.ORDER, t

    def test_send_tools(self):
        for t in ("gmail_send", "discord_send", "slack_send"):
            assert get_level(t) == Level.SEND, t

    def test_unknown_tool_defaults_write(self):
        """알 수 없는 도구는 보수적으로 WRITE."""
        assert get_level("totally_unknown_tool_xyz") == Level.WRITE
        # No exposed iMessage importer tool is registered in this worktree; the
        # docs-only imessage_search reference must not be granted READ implicitly.
        assert get_level("imessage_search") == Level.WRITE

    def test_code_exec_tools_write(self):
        """host_exec/bash/python은 자체 ask 흐름 → 여기선 WRITE."""
        for t in ("host_exec", "bash_exec", "python_exec"):
            assert get_level(t) == Level.WRITE, t


class TestRequiresConsent:
    def test_default_policy_blocks_dangerous(self):
        """기본 정책: 삭제/주문/전송은 consent 필요."""
        assert requires_consent("file_delete")
        assert requires_consent("kis_order_execute")
        assert requires_consent("gmail_send")

    def test_default_policy_allows_safe(self):
        """읽기/쓰기는 기본 정책에서 consent 불필요."""
        assert not requires_consent("web_search")
        assert not requires_consent("gmail_draft")

    def test_custom_policy(self):
        """정책에 WRITE를 추가하면 쓰기도 consent 필요."""
        policy = {Level.WRITE, Level.DELETE, Level.ORDER, Level.SEND}
        assert requires_consent("gmail_draft", policy)
        assert not requires_consent("web_search", policy)


class TestLevelMeta:
    def test_meta_shape(self):
        m = level_meta("gmail_send")
        assert m["level"] == "SEND"
        assert m["value"] == int(Level.SEND)
        assert "label" in m and "color" in m and "badge" in m

    def test_meta_read(self):
        m = level_meta("web_search")
        assert m["level"] == "READ"
        assert m["label"] == "읽기"
