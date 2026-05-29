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
    KEEP_RECENT,
    _estimate_tokens,
    _needs_compaction,
    compact_history,
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
