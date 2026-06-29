# Created: 2026-06-15
# Purpose: streaming.py 핵심 경로 커버리지 (INT-1526) — 63% → 80%
# Dependencies: pipeline/streaming.py, pytest-asyncio
# Test Status: 신규

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# _build_workdir_section  (L160-171)
# ---------------------------------------------------------------------------
class TestBuildWorkdirSection:
    def _fn(self):
        from pipeline.streaming import _build_workdir_section
        return _build_workdir_section

    def test_none_returns_empty(self):
        assert self._fn()(None) == ""

    def test_missing_dir_returns_warning(self, tmp_path):
        missing = str(tmp_path / "nonexistent_xyz")
        result = self._fn()(missing)
        assert "존재하지 않음" in result

    def test_existing_dir_lists_entries(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hi")
        (tmp_path / "subdir").mkdir()
        result = self._fn()(str(tmp_path))
        assert "작업 폴더" in result
        assert "readme.txt" in result

    def test_empty_dir_shows_empty_label(self, tmp_path):
        result = self._fn()(str(tmp_path))
        assert "빈 폴더" in result

    def test_unreadable_dir_shows_fallback(self, tmp_path):
        import os
        (tmp_path / "f.txt").write_text("x")
        orig_iterdir = Path.iterdir

        def bad_iterdir(self):
            raise PermissionError("denied")

        with patch.object(Path, "iterdir", bad_iterdir):
            result = self._fn()(str(tmp_path))
        assert "목록 조회 실패" in result


# ---------------------------------------------------------------------------
# build_system  (L179-) 빌드 경로 분기
# ---------------------------------------------------------------------------
class TestBuildSystem:
    def test_commands_exception_does_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
        with patch("pipeline.streaming.get_persona", return_value="테스트 페르소나"):
            with patch("pipeline.commands.format_commands_for_prompt", side_effect=ImportError):
                from pipeline.streaming import build_system
                result = build_system()
        assert isinstance(result, str)

    def test_agent_md_missing_still_returns_string(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
        with patch("pipeline.streaming.get_persona", return_value=""):
            from pipeline.streaming import build_system
            result = build_system(working_dir=str(tmp_path))
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _queue_put  (L313-321)
# ---------------------------------------------------------------------------
class TestQueuePut:
    def test_puts_to_asyncio_queue_via_loop(self):
        from pipeline.streaming import _queue_put

        async def _run():
            q: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_event_loop()
            _queue_put(q, "hello", loop)
            await asyncio.sleep(0)
            return q.get_nowait()

        result = asyncio.run(_run())
        assert result == "hello"

    def test_puts_directly_without_loop(self):
        from pipeline.streaming import _queue_put
        import queue as _queue
        q = _queue.Queue()
        _queue_put(q, "world", loop=None)
        assert q.get_nowait() == "world"

    def test_loop_runtime_error_falls_back(self):
        from pipeline.streaming import _queue_put
        import queue as _queue

        mock_loop = MagicMock()
        mock_loop.call_soon_threadsafe.side_effect = RuntimeError("closed")
        q = _queue.Queue()
        _queue_put(q, "fallback", loop=mock_loop)
        assert q.get_nowait() == "fallback"


# ---------------------------------------------------------------------------
# _iter_sse_lines  (L300-310)
# ---------------------------------------------------------------------------
class TestIterSseLines:
    def test_yields_complete_lines(self):
        from pipeline.streaming import _iter_sse_lines

        class FakeResp:
            def __init__(self, data: bytes):
                self._data = data
                self._pos = 0

            def read(self, n: int) -> bytes:
                chunk = self._data[self._pos:self._pos + n]
                self._pos += n
                return chunk

        raw = b"data: hello\ndata: world\n"
        resp = FakeResp(raw)
        lines = list(_iter_sse_lines(resp))
        assert lines == [b"data: hello\n", b"data: world\n"]

    def test_empty_response_yields_nothing(self):
        from pipeline.streaming import _iter_sse_lines

        class EmptyResp:
            def read(self, n):
                return b""

        assert list(_iter_sse_lines(EmptyResp())) == []


# ---------------------------------------------------------------------------
# _stream_sse — chat_completions 파서 (L456-497)
# ---------------------------------------------------------------------------
class TestStreamSseChatCompletions:
    """mock SSE lines → token/tool queues"""

    def _make_req(self, url="http://localhost:1234/v1/chat/completions"):
        import urllib.request
        req = urllib.request.Request(url, data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json")
        return req

    def _run_sse(self, lines: list[str], kind: str = "chat_completions"):
        """Feed fake SSE lines into _stream_sse via a mocked http.client."""
        import queue as _queue
        from pipeline.streaming import _stream_sse

        token_q: _queue.Queue = _queue.Queue()
        tool_q: _queue.Queue = _queue.Queue()
        stats: dict = {}

        class FakeConn:
            def connect(self): pass
            def settimeout(self, t): pass
            @property
            def sock(self): return self
            def request(self, *a, **kw): pass
            def getresponse(self): return self
            @property
            def status(self): return 200
            def read(self, n):
                return b""

        raw_bytes = "\n".join(lines).encode() + b"\n"

        class FakeResp:
            def __init__(self):
                self._data = raw_bytes
                self._pos = 0

            @property
            def status(self):
                return 200

            def read(self, n):
                chunk = self._data[self._pos:self._pos + n]
                self._pos += n
                return chunk

        fake_resp = FakeResp()

        import http.client
        with patch.object(http.client, "HTTPConnection") as MockConn:
            inst = MagicMock()
            inst.connect = lambda: None
            inst.sock = MagicMock()
            inst.request = lambda *a, **kw: None
            inst.getresponse = lambda: fake_resp
            MockConn.return_value = inst
            with patch("pipeline.streaming.certified_context", return_value=None):
                _stream_sse(self._make_req(), token_q, tool_q, kind, stats, loop=None)

        tokens = []
        while not token_q.empty():
            t = token_q.get_nowait()
            if t is not None:
                tokens.append(t)
        tools = []
        while not tool_q.empty():
            tc = tool_q.get_nowait()
            if tc is not None:
                tools.append(tc)
        return tokens, tools, stats

    def test_chat_completions_token_extracted(self):
        lines = [
            'data: {"choices": [{"delta": {"content": "안녕"}}]}',
            "data: [DONE]",
        ]
        tokens, tools, _ = self._run_sse(lines)
        assert "안녕" in tokens

    def test_chat_completions_done_emits_pending_tool(self):
        lines = [
            'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": "{\\"q\\": \\"test\\"}"}}]}}]}',
            "data: [DONE]",
        ]
        tokens, tools, _ = self._run_sse(lines)
        assert len(tools) == 1
        assert tools[0]["name"] == "web_search"

    def test_chat_completions_usage_captured(self):
        lines = [
            'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}',
            "data: [DONE]",
        ]
        _, _, stats = self._run_sse(lines)
        assert stats.get("input_tokens") == 10
        assert stats.get("output_tokens") == 5

    def test_invalid_json_chunk_skipped(self):
        lines = [
            "data: not_json_at_all",
            'data: {"choices": [{"delta": {"content": "ok"}}]}',
            "data: [DONE]",
        ]
        tokens, _, _ = self._run_sse(lines)
        assert "ok" in tokens

    def test_non_data_lines_ignored(self):
        lines = [
            ": keep-alive",
            "",
            'data: {"choices": [{"delta": {"content": "hi"}}]}',
            "data: [DONE]",
        ]
        tokens, _, _ = self._run_sse(lines)
        assert "hi" in tokens


# ---------------------------------------------------------------------------
# _stream_sse — anthropic 파서 (L398-454)
# ---------------------------------------------------------------------------
class TestStreamSseAnthropic:
    def _run_anthropic(self, events: list[dict]):
        import queue as _queue
        from pipeline.streaming import _stream_sse
        import urllib.request

        req = urllib.request.Request("http://x.test/v1/messages", data=b"{}", method="POST")
        lines = [f"data: {json.dumps(ev)}" for ev in events]
        raw_bytes = "\n".join(lines).encode() + b"\n"

        token_q: _queue.Queue = _queue.Queue()
        tool_q: _queue.Queue = _queue.Queue()
        stats: dict = {}

        class FakeResp:
            _data = raw_bytes
            _pos = 0
            status = 200

            def read(self, n):
                chunk = self._data[self._pos:self._pos + n]
                self._pos += n
                return chunk

        import http.client
        with patch.object(http.client, "HTTPConnection") as MockConn:
            inst = MagicMock()
            inst.sock = MagicMock()
            inst.getresponse = lambda: FakeResp()
            MockConn.return_value = inst
            with patch("pipeline.streaming.certified_context", return_value=None):
                _stream_sse(req, token_q, tool_q, "anthropic", stats, loop=None)

        tokens, tools = [], []
        while not token_q.empty():
            t = token_q.get_nowait()
            if t is not None:
                tokens.append(t)
        while not tool_q.empty():
            tc = tool_q.get_nowait()
            if tc is not None:
                tools.append(tc)
        return tokens, tools, stats

    def test_text_delta_yields_token(self):
        tokens, _, _ = self._run_anthropic([
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
            {"type": "message_stop"},
        ])
        assert "Hello" in tokens

    def test_tool_use_emitted_at_stop(self):
        _, tools, _ = self._run_anthropic([
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t1", "name": "web_search"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"q":'}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '"hello"}'}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_stop"},
        ])
        assert len(tools) == 1
        assert tools[0]["name"] == "web_search"
        assert '"hello"' in tools[0]["arguments"]

    def test_message_start_captures_usage(self):
        _, _, stats = self._run_anthropic([
            {"type": "message_start", "message": {"usage": {"input_tokens": 20, "cache_read_input_tokens": 5}}},
            {"type": "message_stop"},
        ])
        assert stats.get("input_tokens") == 20
        assert stats.get("cached_tokens") == 5

    def test_error_event_stops_stream(self):
        tokens, _, _ = self._run_anthropic([
            {"type": "error", "error": {"message": "overloaded"}},
        ])
        assert tokens == []


# ---------------------------------------------------------------------------
# stream_gpt — 단일 라운드 mock (L561-786)
# ---------------------------------------------------------------------------
class TestStreamGptLoop:
    """stream_gpt with mocked _build_request + _stream_sse via executor."""

    def _make_sse_mock(self, token_text: str = "응답", tools: list[dict] | None = None):
        """Returns a side_effect for loop.run_in_executor that fills queues."""

        def _fake_sse(req, token_q, tool_q, kind, stats, loop_arg):
            import queue as _queue
            token_q.put_nowait(token_text)
            token_q.put_nowait(None)
            if tools:
                for tc in tools:
                    tool_q.put_nowait(tc)
            tool_q.put_nowait(None)

        return _fake_sse

    def _build_req_mock(self):
        import urllib.request
        req = urllib.request.Request("http://fake/v1/responses", data=b"{}", method="POST")
        return req, "responses"

    @pytest.mark.asyncio
    async def test_single_round_returns_text(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))

        tokens_seen = []

        async def on_token(t):
            tokens_seen.append(t)

        with patch("pipeline.streaming._build_request", return_value=self._build_req_mock()):
            with patch("pipeline.streaming._stream_sse", side_effect=self._make_sse_mock("안녕")):
                with patch("pipeline.streaming.build_system", return_value="sys"):
                    with patch("pipeline.streaming.build_dynamic_preamble", return_value=""):
                        from pipeline.streaming import stream_gpt
                        result = await stream_gpt(
                            [{"role": "user", "content": "hi"}],
                            system="sys",
                            on_token=on_token,
                            stats={},
                        )

        assert result == "안녕"
        assert "안녕" in tokens_seen

    @pytest.mark.asyncio
    async def test_multi_turn_history_joined(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))

        async def on_token(t): pass

        messages = [
            {"role": "user", "content": "첫 메시지"},
            {"role": "assistant", "content": "응답"},
            {"role": "user", "content": "두 번째"},
        ]

        captured_input = []

        def fake_build_request(input_items, system, ce_mode=False, research_mode=False,
                               tier=None, model_override=None, load=None, **kwargs):
            captured_input.extend(input_items)
            return self._build_req_mock()

        with patch("pipeline.streaming._build_request", side_effect=fake_build_request):
            with patch("pipeline.streaming._stream_sse", side_effect=self._make_sse_mock("ok")):
                with patch("pipeline.streaming.build_dynamic_preamble", return_value=""):
                    from pipeline.streaming import stream_gpt
                    await stream_gpt(messages, system="sys", on_token=on_token, stats={})

        first_user_content = captured_input[0]["content"]
        assert "대화 히스토리" in first_user_content
        assert "첫 메시지" in first_user_content

    @pytest.mark.asyncio
    async def test_stream_error_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))

        async def on_token(t): pass

        def broken_sse(req, token_q, tool_q, kind, stats, loop_arg):
            if stats is not None:
                stats["stream_error"] = "connection refused"
            token_q.put_nowait(None)
            tool_q.put_nowait(None)

        with patch("pipeline.streaming._build_request", return_value=self._build_req_mock()):
            with patch("pipeline.streaming._stream_sse", side_effect=broken_sse):
                with patch("pipeline.streaming.build_dynamic_preamble", return_value=""):
                    from pipeline.streaming import stream_gpt
                    with pytest.raises(RuntimeError, match="LLM 스트림 오류"):
                        await stream_gpt(
                            [{"role": "user", "content": "hi"}],
                            system="sys",
                            on_token=on_token,
                            stats={},
                        )

    @pytest.mark.asyncio
    async def test_tool_dispatch_called(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))

        tool_calls_seen = []
        async def on_tool_start(name, args, call_id):
            tool_calls_seen.append(name)

        call_count = 0

        def counting_sse(req, token_q, tool_q, kind, stats, loop_arg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 첫 라운드: tool 발행
                tool_q.put_nowait({"id": "t1", "call_id": "c1", "name": "web_search", "arguments": '{"q":"test"}'})
            token_q.put_nowait(None)
            tool_q.put_nowait(None)

        with patch("pipeline.streaming._build_request", return_value=self._build_req_mock()):
            with patch("pipeline.streaming._stream_sse", side_effect=counting_sse):
                with patch("pipeline.streaming.build_dynamic_preamble", return_value=""):
                    with patch("pipeline.streaming.dispatch_tool", return_value='{"result": "ok"}'):
                        from pipeline.streaming import stream_gpt
                        await stream_gpt(
                            [{"role": "user", "content": "search"}],
                            system="sys",
                            on_token=lambda t: None,
                            on_tool_start=on_tool_start,
                            stats={},
                        )

        assert "web_search" in tool_calls_seen

    @pytest.mark.asyncio
    async def test_consent_denied_skips_execution(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))

        dispatched = []
        call_count = 0

        def counting_sse(req, token_q, tool_q, kind, stats, loop_arg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                tool_q.put_nowait({"id": "t1", "call_id": "c1", "name": "send_email", "arguments": "{}"})
            token_q.put_nowait(None)
            tool_q.put_nowait(None)

        async def deny_all(name, args, call_id):
            return False

        with patch("pipeline.streaming._build_request", return_value=self._build_req_mock()):
            with patch("pipeline.streaming._stream_sse", side_effect=counting_sse):
                with patch("pipeline.streaming.build_dynamic_preamble", return_value=""):
                    with patch("pipeline.streaming.dispatch_tool", side_effect=lambda n, a: dispatched.append(n) or "{}"):
                        with patch("pipeline.permission.requires_consent", return_value=True):
                            from pipeline.streaming import stream_gpt
                            await stream_gpt(
                                [{"role": "user", "content": "send"}],
                                system="sys",
                                on_token=lambda t: None,
                                on_consent=deny_all,
                                stats={},
                            )

        assert "send_email" not in dispatched

    @pytest.mark.asyncio
    async def test_stats_timing_populated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))

        stats = {"output_tokens": 10}

        async def noop_token(t): pass

        with patch("pipeline.streaming._build_request", return_value=self._build_req_mock()):
            with patch("pipeline.streaming._stream_sse", side_effect=self._make_sse_mock("hi")):
                with patch("pipeline.streaming.build_dynamic_preamble", return_value=""):
                    from pipeline.streaming import stream_gpt
                    await stream_gpt(
                        [{"role": "user", "content": "go"}],
                        system="sys",
                        on_token=noop_token,
                        stats=stats,
                    )

        assert "elapsed_sec" in stats
        assert "tok_per_sec" in stats
        assert "ttft_sec" in stats
