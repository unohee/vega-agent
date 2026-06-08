# Created: 2026-05-21
# Purpose: pipeline/streaming.py 단위 테스트 — 순수 함수 + mock 기반
# Dependencies: pipeline/streaming.py
# Test Status: 신규

from __future__ import annotations

import json
import queue
import time
from unittest.mock import MagicMock, patch

import pytest


class TestCollectSafe:
    """_collect_safe — timeout 방어 래퍼"""

    def setup_method(self):
        from pipeline.streaming import _collect_safe
        self.collect_safe = _collect_safe

    def test_normal_return(self):
        result = self.collect_safe(lambda: {"a": 1})
        assert result == {"a": 1}

    def test_timeout_returns_default(self):
        def slow():
            time.sleep(0.2)
            return {"data": "never"}

        start = time.monotonic()
        result = self.collect_safe(slow, timeout=0.01, default={})
        elapsed = time.monotonic() - start

        assert result == {}
        assert elapsed < 0.1

    def test_exception_returns_default(self):
        def boom():
            raise RuntimeError("DB 연결 실패")

        result = self.collect_safe(boom, default=[])
        assert result == []

    def test_passes_args(self):
        def add(x, y):
            return x + y

        assert self.collect_safe(add, 3, 4) == 7

    def test_passes_kwargs(self):
        def greet(name="world"):
            return f"hello {name}"

        assert self.collect_safe(greet, name="vega") == "hello vega"


class TestBuildDashboardContext:
    """_build_dashboard_context — 각 수집 실패 시 graceful degradation"""

    def test_all_empty_returns_empty_string(self):
        from pipeline.streaming import _build_dashboard_context
        with patch("pipeline.context_collect.collect_calendar", return_value={}):
            with patch("pipeline.context_collect.collect_linear_in_progress", return_value=[]):
                with patch("pipeline.context_collect.collect_priority_mail_since", return_value=[]):
                    result = _build_dashboard_context()
        assert isinstance(result, str)

    def test_calendar_hang_does_not_block(self):
        """calendar가 timeout해도 결과 반환 (병렬 수집 구조)."""
        from pipeline import streaming

        import time as _time

        def slow_calendar(*args, **kwargs):
            _time.sleep(0.2)
            return {}

        start = _time.monotonic()
        with patch("pipeline.streaming._COLLECT_TIMEOUT", 0.01):
            with patch("pipeline.context_collect.collect_calendar", side_effect=slow_calendar):
                with patch("pipeline.context_collect.collect_linear_in_progress", return_value=[]):
                    with patch("pipeline.context_collect.collect_priority_mail_since", return_value=[]):
                        result = streaming._build_dashboard_context()
        elapsed = _time.monotonic() - start

        assert isinstance(result, str)
        assert elapsed < 0.1

    def test_linear_issues_rendered(self):
        from pipeline.streaming import _build_dashboard_context

        fake_issues = [
            {"identifier": "RES-213", "title": "streaming 리팩터링", "project": "VEGA Agent", "team": "Research", "due_date": None}
        ]

        with patch("pipeline.context_collect.collect_calendar", return_value={}):
            with patch("pipeline.context_collect.collect_linear_in_progress", return_value=fake_issues):
                with patch("pipeline.context_collect.collect_priority_mail_since", return_value=[]):
                    result = _build_dashboard_context()
        assert "RES-213" in result
        assert "streaming 리팩터링" in result

    def test_high_priority_mail_badge(self):
        from pipeline.streaming import _build_dashboard_context

        fake_mails = [
            {"priority": "high", "subject": "긴급 계약", "sender": "partner@example.com"},
            {"priority": "medium", "subject": "회의 안내", "sender": "team@example.com"},
        ]

        with patch("pipeline.context_collect.collect_calendar", return_value={}):
            with patch("pipeline.context_collect.collect_linear_in_progress", return_value=[]):
                with patch("pipeline.context_collect.collect_priority_mail_since", return_value=fake_mails):
                    result = _build_dashboard_context()
        assert "🔴" in result
        assert "긴급 계약" in result
        assert "🟡" in result


class TestGetDashboardContext:
    """_get_dashboard_context — TTL 캐시"""

    def test_caches_result(self):
        from pipeline import streaming

        # 캐시 초기화
        streaming._DASHBOARD_CACHE = None
        call_count = [0]

        def fake_build():
            call_count[0] += 1
            return "대시보드 내용"

        with patch("pipeline.streaming._build_dashboard_context", side_effect=fake_build):
            r1 = streaming._get_dashboard_context()
            r2 = streaming._get_dashboard_context()

        assert r1 == r2 == "대시보드 내용"
        assert call_count[0] == 1  # 두 번 호출해도 build는 1회

    def test_cache_expires(self):
        from pipeline import streaming
        streaming._DASHBOARD_CACHE = (time.monotonic() - streaming._DASHBOARD_TTL - 1, "낡은 캐시")

        with patch("pipeline.streaming._build_dashboard_context", return_value="새 내용"):
            result = streaming._get_dashboard_context()
        assert result == "새 내용"

    def test_returns_cached_within_ttl(self):
        from pipeline import streaming
        streaming._DASHBOARD_CACHE = (time.monotonic(), "유효한 캐시")

        with patch("pipeline.streaming._build_dashboard_context", return_value="이걸 쓰면 안 됨") as mock_build:
            result = streaming._get_dashboard_context()
        mock_build.assert_not_called()
        assert result == "유효한 캐시"


class TestBuildSystem:
    """build_system — 시스템 프롬프트 생성 + truncation"""

    def test_returns_string(self):
        from pipeline.streaming import build_system
        with patch("pipeline.streaming._get_dashboard_context", return_value=""):
            with patch("pipeline.streaming._get_state_context", return_value=""):
                with patch("pipeline.streaming.get_persona", return_value="페르소나"):
                    result = build_system()
        assert isinstance(result, str)
        assert "VEGA" in result

    def test_dashboard_truncated_when_too_long(self):
        """dashboard는 이제 build_system이 아니라 build_dynamic_preamble로 분리됨
        (prompt caching 친화). truncation 동작은 preamble에서 검증."""
        from pipeline import streaming
        long_dashboard = "X" * 4000  # _DASHBOARD_MAX_CHARS(3000) 초과

        with patch("pipeline.streaming._get_dashboard_context", return_value=long_dashboard):
            with patch("pipeline.streaming._get_state_context", return_value=""):
                result = streaming.build_dynamic_preamble()

        # 생략 표시가 있어야 함
        assert "생략됨" in result
        # 원본 4000자가 그대로 들어가 있으면 안 됨
        assert "X" * 4000 not in result

    def test_empty_dashboard_skips_section(self):
        from pipeline import streaming
        streaming._PERSONA_CACHE = "페르소나"

        with patch("pipeline.streaming._get_dashboard_context", return_value=""):
            with patch("pipeline.streaming._get_state_context", return_value=""):
                result = streaming.build_system()
        assert "현재 상황 브리핑" not in result

    def test_persona_cached(self):
        from pipeline import streaming
        streaming._PERSONA_CACHE = None

        with patch("pipeline.streaming.get_persona", return_value="캐시 테스트 페르소나") as mock_persona:
            with patch("pipeline.streaming._get_dashboard_context", return_value=""):
                with patch("pipeline.streaming._get_state_context", return_value=""):
                    streaming.build_system()
                    streaming.build_system()  # 두 번째 호출

        # get_persona는 첫 호출 시만 실행
        mock_persona.assert_called_once()


class TestBuildDashboardContextCalendar:
    """_build_dashboard_context — calendar 이벤트 렌더링 (lines 73-77)"""

    def test_calendar_events_rendered(self):
        from pipeline.streaming import _build_dashboard_context
        from datetime import date

        today_str = date.today().isoformat()
        fake_events = {today_str: ["10:00 회의", "14:00 점심"]}

        with patch("pipeline.context_collect.collect_calendar", return_value=fake_events):
            with patch("pipeline.context_collect.collect_linear_in_progress", return_value=[]):
                with patch("pipeline.context_collect.collect_priority_mail_since", return_value=[]):
                    result = _build_dashboard_context()
        assert today_str in result
        assert "오늘" in result
        assert "10:00 회의" in result

    def test_non_today_calendar_no_today_tag(self):
        from pipeline.streaming import _build_dashboard_context

        fake_events = {"2030-01-01": ["이벤트A"]}

        with patch("pipeline.context_collect.collect_calendar", return_value=fake_events):
            with patch("pipeline.context_collect.collect_linear_in_progress", return_value=[]):
                with patch("pipeline.context_collect.collect_priority_mail_since", return_value=[]):
                    result = _build_dashboard_context()
        assert "2030-01-01" in result
        assert "오늘" not in result


class TestBuildRequestRaisesWithoutProfile:
    """_build_request — 프로파일 없을 때 RuntimeError. ChatGPT 프로바이더 강제."""

    _CHATGPT_PROV = {
        "name": "chatgpt",
        "kind": "responses",
        "auth_type": "chatgpt_oauth",
        "base_url": "https://chatgpt.com/backend-api/codex/responses",
        "default_model": "gpt-5.5",
        "extra_headers": {"originator": "vega", "OpenAI-Beta": "responses=experimental"},
    }

    def test_raises_without_profile(self):
        from pipeline.streaming import _build_request
        with patch("pipeline.llm_gateway.get_active_provider", return_value=self._CHATGPT_PROV):
            with patch("pipeline.auth.chatgpt._load_profile", return_value=None):
                with pytest.raises(RuntimeError, match="OAuth"):
                    _build_request([], "system")

    def test_returns_request_with_profile(self):
        from pipeline.streaming import _build_request
        import urllib.request as _ur
        fake_profile = {"account_id": "acc123", "access_token": "tok", "expires_at": 9999999999}
        with patch("pipeline.llm_gateway.get_active_provider", return_value=self._CHATGPT_PROV):
            with patch("pipeline.auth.chatgpt._load_profile", return_value=fake_profile):
                with patch("pipeline.auth.chatgpt.ensure_valid_token", return_value="tok"):
                    req, kind = _build_request([{"role": "user", "content": "hi"}], "sys")
        assert isinstance(req, _ur.Request)
        assert kind == "responses"


class TestGetStateContext:
    """_get_state_context — TTL 캐시 + 예외 방어"""

    def test_caches_result(self):
        from pipeline import streaming
        streaming._STATE_CACHE = None

        with patch("pipeline.streaming.render_state_for_prompt", return_value="프로젝트 상태", create=True):
            with patch("pipeline.project_state.render_state_for_prompt", return_value="프로젝트 상태"):
                r1 = streaming._get_state_context()
                r2 = streaming._get_state_context()
        assert r1 == r2

    def test_exception_returns_empty(self):
        from pipeline import streaming
        streaming._STATE_CACHE = None

        with patch("pipeline.project_state.render_state_for_prompt", side_effect=Exception("DB 없음")):
            result = streaming._get_state_context()
        assert result == ""

    def test_cache_expires(self):
        from pipeline import streaming
        streaming._STATE_CACHE = (time.monotonic() - streaming._STATE_TTL - 1, "낡은 상태")

        with patch("pipeline.project_state.render_state_for_prompt", return_value="최신 상태"):
            result = streaming._get_state_context()
        assert result == "최신 상태"


class TestStreamSSE:
    """_stream_sse — SSE 파싱 로직 (HTTP mock)"""

    def _make_sse_lines(self, events: list[dict]) -> list[bytes]:
        lines = []
        for ev in events:
            lines.append(f"data: {json.dumps(ev)}\n".encode())
        lines.append(b"data: [DONE]\n")
        return lines

    def test_token_delta_queued(self):
        from pipeline.streaming import _stream_sse

        events = [
            {"type": "response.output_text.delta", "delta": "안"},
            {"type": "response.output_text.delta", "delta": "녕"},
        ]
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.__iter__ = lambda s: iter(self._make_sse_lines(events))

        token_q: queue.Queue = queue.Queue()
        tool_q: queue.Queue = queue.Queue()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            _stream_sse(MagicMock(), token_q, tool_q)

        tokens = []
        while not token_q.empty():
            t = token_q.get_nowait()
            if t is not None:
                tokens.append(t)
        assert "".join(tokens) == "안녕"

    def test_sentinel_none_always_put(self):
        """에러 발생 시에도 sentinel None이 두 큐에 put된다."""
        from pipeline.streaming import _stream_sse

        token_q: queue.Queue = queue.Queue()
        tool_q: queue.Queue = queue.Queue()

        with patch("urllib.request.urlopen", side_effect=Exception("연결 실패")):
            _stream_sse(MagicMock(), token_q, tool_q)

        # sentinel이 있어야 소비 루프가 종료됨
        assert token_q.get_nowait() is None
        assert tool_q.get_nowait() is None

    def test_tool_call_assembled(self):
        """function_call 이벤트가 arguments.done까지 조립된다."""
        from pipeline.streaming import _stream_sse

        events = [
            {"type": "response.output_item.added", "item": {
                "type": "function_call", "id": "item1",
                "call_id": "call1", "name": "web_search"
            }},
            {"type": "response.function_call_arguments.delta",
             "item_id": "item1", "delta": '{"query":'},
            {"type": "response.function_call_arguments.delta",
             "item_id": "item1", "delta": '"VEGA"}'},
            {"type": "response.function_call_arguments.done",
             "item_id": "item1", "arguments": '{"query":"VEGA"}'},
        ]
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.__iter__ = lambda s: iter(self._make_sse_lines(events))

        token_q: queue.Queue = queue.Queue()
        tool_q: queue.Queue = queue.Queue()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            _stream_sse(MagicMock(), token_q, tool_q)

        tc = tool_q.get_nowait()
        assert tc["name"] == "web_search"
        assert tc["call_id"] == "call1"
        assert json.loads(tc["arguments"])["query"] == "VEGA"


class TestStreamGptStability:
    """stream_gpt — provider stream failure handling."""

    @pytest.mark.asyncio
    async def test_empty_stream_error_is_not_silent_done(self):
        from pipeline import streaming

        def fake_stream_sse(req, token_q, tool_q, kind="chat_completions", stats_out=None, loop=None):
            if stats_out is not None:
                stats_out["stream_error"] = "timed out"
            streaming._queue_put(token_q, None, loop)
            streaming._queue_put(tool_q, None, loop)

        tokens: list[str] = []
        stats: dict = {}
        with patch("pipeline.streaming._build_request", return_value=(MagicMock(), "chat_completions")):
            with patch("pipeline.streaming._stream_sse", side_effect=fake_stream_sse):
                with patch("pipeline.streaming.build_dynamic_preamble", return_value=""):
                    with pytest.raises(RuntimeError, match="LLM 스트림 오류"):
                        await streaming.stream_gpt(
                            [{"role": "user", "content": "hi"}],
                            "system",
                            on_token=lambda tok: tokens.append(tok),
                            stats=stats,
                        )

        assert tokens == []
        assert stats["stream_error"] == "timed out"
