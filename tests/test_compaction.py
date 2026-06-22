# Created: 2026-05-21
# Purpose: pipeline/compaction.py 단위 테스트 — 순수 함수 + mock 기반 async
# Dependencies: pipeline/compaction.py
# Test Status: 신규

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.compaction import (
    COMPACT_THRESHOLD,
    COMPACT_TOKEN_THRESHOLD,
    KEEP_RECENT,
    _call_compact_sync,
    _estimate_tokens,
    _needs_compaction,
    compact_history,
    splice_compacted,
)


class TestEstimateTokens:
    """_estimate_tokens는 이제 tiktoken(cl100k_base)으로 정확 계산.
    + 메시지당 role 오버헤드 ~4 토큰 추가."""

    def test_empty(self):
        assert _estimate_tokens([]) == 0

    def test_single_message(self):
        history = [{"role": "user", "content": "안녕하세요"}]
        # 정확한 토큰 수는 tokenizer 의존이지만 양수여야
        n = _estimate_tokens(history)
        assert n > 0 and n < 50

    def test_multiple_messages(self):
        history = [
            {"role": "user", "content": "a" * 300},
            {"role": "assistant", "content": "b" * 300},
        ]
        # "aaaa..." 300자는 ~75 토큰 정도 + 2메시지 × 4 오버헤드
        n = _estimate_tokens(history)
        assert n > 100 and n < 300

    def test_missing_content(self):
        # content 키 없어도 role 오버헤드 4
        history = [{"role": "user"}]
        n = _estimate_tokens(history)
        assert 0 <= n <= 10

    def test_non_string_content(self):
        history = [{"role": "user", "content": ["item1", "item2"]}]
        result = _estimate_tokens(history)
        assert isinstance(result, int)
        assert result >= 0


class TestNeedsCompaction:
    def test_below_threshold(self):
        history = [{"role": "user", "content": "x"}] * (COMPACT_THRESHOLD - 1)
        assert _needs_compaction(history) is False

    def test_at_threshold(self):
        history = [{"role": "user", "content": "x"}] * COMPACT_THRESHOLD
        assert _needs_compaction(history) is True

    def test_above_threshold(self):
        history = [{"role": "user", "content": "x"}] * (COMPACT_THRESHOLD + 5)
        assert _needs_compaction(history) is True

    def test_empty_history(self):
        assert _needs_compaction([]) is False


class TestCompactHistory:
    """compact_history async 함수 — _call_compact_sync를 mock으로 대체."""

    def _make_history(self, n: int) -> list[dict]:
        msgs = []
        for i in range(n):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({"role": role, "content": f"메시지 {i}"})
        return msgs

    @pytest.mark.asyncio
    async def test_compact_success(self):
        history = self._make_history(COMPACT_THRESHOLD + 2)
        mock_return = ("요약 텍스트", [])
        with patch("pipeline.compaction._call_compact_sync", return_value=mock_return):
            new_hist, summary = await compact_history(history)
        assert summary == "요약 텍스트"
        assert len(new_hist) == KEEP_RECENT + 1  # 요약블록 + recent

    @pytest.mark.asyncio
    async def test_compact_failure_fallback(self):
        history = self._make_history(COMPACT_THRESHOLD)
        with patch("pipeline.compaction._call_compact_sync", side_effect=Exception("API 오류")):
            new_hist, summary = await compact_history(history)
        # 실패 시 요약 실패 메시지 포함
        assert "실패" in summary or "요약" in summary
        assert isinstance(new_hist, list)

    @pytest.mark.asyncio
    async def test_compact_with_status_callback(self):
        history = self._make_history(COMPACT_THRESHOLD)
        status_calls = []

        async def on_status(msg):
            status_calls.append(msg)

        mock_return = ("요약", [])
        with patch("pipeline.compaction._call_compact_sync", return_value=mock_return):
            await compact_history(history, on_status=on_status)
        assert len(status_calls) >= 1

    @pytest.mark.asyncio
    async def test_compact_short_history(self):
        # KEEP_RECENT보다 짧은 히스토리
        history = self._make_history(3)
        mock_return = ("요약", [])
        with patch("pipeline.compaction._call_compact_sync", return_value=mock_return):
            new_hist, summary = await compact_history(history)
        assert len(new_hist) >= 1


class TestConstants:
    def test_threshold_positive(self):
        assert COMPACT_THRESHOLD > 0

    def test_keep_recent_less_than_threshold(self):
        assert KEEP_RECENT < COMPACT_THRESHOLD


class TestTokenBasedTrigger:
    """INT-1430 — 트리거가 토큰 기준(주) + 메시지 수(백스톱)로 동작하는지."""

    def test_short_messages_below_token_threshold_no_compaction(self):
        # 짧은 메시지 25개 — 기존 20개 고정 트리거였으면 True였을 케이스.
        # 토큰이 적으므로 압축하지 않아야 한다 (불필요한 요약 LLM 호출 방지).
        history = [{"role": "user", "content": "짧은 메시지"}] * 25
        assert _needs_compaction(history) is False

    def test_token_heavy_history_triggers(self):
        # 메시지 수는 적어도(4개 < 백스톱) 토큰 합이 크면 압축해야 한다
        history = [{"role": "user", "content": "한글내용입니다 " * 1000}] * 4
        assert _estimate_tokens(history) >= COMPACT_TOKEN_THRESHOLD
        assert _needs_compaction(history) is True

    def test_message_count_backstop(self):
        history = [{"role": "user", "content": "x"}] * COMPACT_THRESHOLD
        assert _needs_compaction(history) is True


class TestSpliceCompacted:
    """백그라운드 압축 완료 시 in-place 접합 — 압축 중 도착 메시지 보존 (INT-1430)."""

    def _live(self, n: int) -> list[dict]:
        return [{"role": "user", "content": f"m{i}"} for i in range(n)]

    def test_basic_splice(self):
        live = self._live(10)
        summary = {"role": "assistant", "content": "[요약]"}
        splice_compacted(live, summary, 4)
        assert live[0] is summary
        assert [m["content"] for m in live[1:]] == [f"m{i}" for i in range(4, 10)]

    def test_messages_added_during_compaction_preserved(self):
        # 스냅샷 후 압축이 도는 동안 live에 새 메시지가 추가된 시나리오
        live = self._live(10)
        n_summarized = 4  # 스냅샷 시점 기준
        live.append({"role": "user", "content": "압축 중 도착한 메시지"})
        splice_compacted(live, {"role": "assistant", "content": "[요약]"}, n_summarized)
        assert live[-1]["content"] == "압축 중 도착한 메시지"
        assert len(live) == 1 + (11 - n_summarized)

    def test_list_identity_preserved(self):
        # _SESSION_HISTORY와 핸들러가 같은 객체를 들고 있으므로 identity 유지 필수
        live = self._live(8)
        ref = live
        splice_compacted(live, {"role": "assistant", "content": "s"}, 2)
        assert ref is live
        assert ref[0]["content"] == "s"


class TestCallCompactSyncBuildRequest:
    """회귀: _build_request 는 (Request, kind) 튜플을 반환한다.
    compaction 이 언패킹하지 않으면 _stream_sse 가 튜플을 받아
    `'tuple' object has no attribute 'full_url'` 로 매 압축마다 조용히 깨진다.
    (INT-1564 계열 — vega-agent 로 역이식 누락되어 회귀했던 버그)
    """

    def test_unpacks_build_request_tuple_and_passes_kind(self):
        fake_req = object()  # Request 대역 — 튜플이면 안 된다
        captured: dict = {}

        def fake_stream_sse(req, token_q, tool_q, kind="responses", **kw):
            captured["req"] = req
            captured["kind"] = kind
            token_q.put(None)  # sentinel — 드레인 루프 종료
            tool_q.put(None)

        with patch(
            "pipeline.compaction._build_request",
            return_value=(fake_req, "chat_completions"),
        ), patch(
            "pipeline.compaction._stream_sse", side_effect=fake_stream_sse
        ):
            summary, calls = _call_compact_sync(
                [{"role": "user", "content": "x"}], "sys"
            )

        # req 는 튜플 전체가 아니라 _build_request()[0] 그 자체여야 한다.
        assert captured["req"] is fake_req
        assert not isinstance(captured["req"], tuple)
        # kind 도 전달돼야 chat_completions 파싱 분기가 맞는다 (안 넘기면 'responses' 기본값).
        assert captured["kind"] == "chat_completions"
        assert summary == ""
        assert calls == []
