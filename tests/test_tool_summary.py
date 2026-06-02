# Created: 2026-06-02
# Purpose: 도구 실행 배지 요약(_exec_summary / _tool_summary) + 중단 시 부분 응답 영속화 회귀 테스트
# Dependencies: web/server.py
# Test Status: 검증 중

from __future__ import annotations

import json

import pytest

from web.server import _build_aborted_message, _exec_summary, _tool_summary


# ── _exec_summary: '무엇을 했는지'(명령어)를 표현해야 한다 ──────────────────────
class TestExecSummary:
    def test_command_shown_not_stdout(self):
        """summary는 stdout 첫 줄이 아니라 실행한 명령어를 보여야 한다."""
        s = _exec_summary("grep -R 'CREATE TABLE' .", "--- roots ---\n/a\n/b", "", 0)
        assert "grep -R" in s
        assert "CREATE TABLE" in s
        # stdout 내용(roots 등)이 새어나오면 안 됨
        assert "roots" not in s

    def test_success_prefix_check(self):
        s = _exec_summary("ls", "a\nb\nc", "", 0)
        assert s.startswith("✓")

    def test_failure_prefix_and_rc(self):
        s = _exec_summary("false", "", "", 1)
        assert s.startswith("✗")

    def test_failure_shows_stderr_tail(self):
        s = _exec_summary("cat nope", "", "cat: nope: No such file", 1)
        assert s.startswith("✗")
        assert "No such file" in s

    def test_long_command_truncated(self):
        long_cmd = "find . " + "-iname '*x*' " * 30
        s = _exec_summary(long_cmd, "", "", 0)
        # 명령어 70자 + 말줄임 + prefix 정도. 과도하게 길면 안 됨
        assert len(s) < 90
        assert s.endswith("…")

    def test_multiline_command_compacted(self):
        s = _exec_summary("git status\n  --short", "", "", 0)
        # 개행이 공백으로 압축돼야 함
        assert "\n" not in s

    def test_no_command_fallback(self):
        """명령어를 모를 때(폴백)도 stdout이 그대로 새어나오면 안 된다."""
        s = _exec_summary("", "엄청난 출력\n많은 줄", "", 0)
        assert s.startswith("✓")


# ── _tool_summary: host_exec 결과 dict → 명령어 기반 요약 ──────────────────────
class TestToolSummary:
    def test_stdout_result_uses_command(self):
        result = json.dumps({"stdout": "/a\n/b\n/c", "stderr": "", "returncode": 0})
        summary, chart = _tool_summary("host_exec", result, command="find . -name '*.py'")
        assert chart is None
        assert "find ." in summary
        # 파일 목록(stdout)이 summary에 들어가면 안 됨
        assert "/a" not in summary

    def test_stdout_in_result_dict_fallback_command(self):
        """command 인자 없이도 result dict의 command 키를 폴백으로 쓴다."""
        result = json.dumps(
            {"stdout": "ok", "stderr": "", "returncode": 0, "command": "echo ok"}
        )
        summary, _ = _tool_summary("host_exec", result)
        assert "echo ok" in summary

    def test_error_result(self):
        result = json.dumps({"error": "권한 없음"})
        summary, chart = _tool_summary("file_read", result)
        assert summary.startswith("✗")
        assert "권한 없음" in summary

    def test_image_result_returns_chart(self):
        result = json.dumps({"__type": "image", "path": "/tmp/chart_abc.png"})
        summary, chart = _tool_summary("chart_matplotlib", result)
        assert chart is not None
        assert chart["type"] == "image"

    def test_list_result_count(self):
        result = json.dumps([1, 2, 3, 4])
        summary, _ = _tool_summary("gmail_search", result)
        assert "4건" in summary

    def test_unparseable_result(self):
        summary, chart = _tool_summary("x", "not json")
        assert summary == "✓ 완료"
        assert chart is None


# ── 중단 시 부분 응답 영속화 ──────────────────────────────────────────────────
# 버그: 도구만 실행되고 텍스트 토큰 없이 중단되면 DB에 아무것도 저장 안 돼
# 세션을 벗어났다 돌아오면 멈춘 블럭이 사라지던 회귀.
class TestAbortedMessage:
    def test_text_only(self):
        msg = _build_aborted_message("부분 답변입니다", [])
        assert "부분 답변입니다" in msg
        assert "중단" in msg

    def test_tool_trace_only_preserved(self):
        """텍스트가 없어도 도구 실행 흔적이 있으면 저장 텍스트가 비지 않아야 한다."""
        msg = _build_aborted_message("", ["✓ grep -R foo rc=0", "✓ ls"])
        assert msg != ""
        assert "grep -R foo" in msg
        assert "ls" in msg
        assert "중단" in msg

    def test_text_and_tools_combined(self):
        msg = _build_aborted_message("진행 중 설명", ["✓ find ."])
        assert "find ." in msg
        assert "진행 중 설명" in msg

    def test_empty_returns_empty(self):
        """텍스트도 도구도 없으면 빈 문자열 → 저장 안 함(user 메시지 pop)."""
        assert _build_aborted_message("", []) == ""
        assert _build_aborted_message("   ", []) == ""

    def test_tools_listed_as_bullets(self):
        msg = _build_aborted_message("", ["✓ a", "✓ b"])
        assert "- ✓ a" in msg
        assert "- ✓ b" in msg
