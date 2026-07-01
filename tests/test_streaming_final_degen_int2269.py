# Created: 2026-07-01
# Purpose: Regression for INT-2269 (d) — final-answer-round degeneration is detected and
#          regenerated with a sturdier model (TECH #4322). The pre-existing tool-round
#          safety net (INT-1999 b) only cleaned tool-round intros; a degenerate FINAL
#          answer was streamed to the user as-is. This covers the new branch inserted at
#          `if not pending_tools:` in stream_gpt: on degen + auto_route it strips the
#          emitted delta from full_text, switches model_override, records stats, and
#          `continue`s to regenerate instead of `break`.

from __future__ import annotations

import asyncio

import pytest

import pipeline.streaming as s


# A degenerate final round: repeated collapse (low vocab diversity, >300 chars) so
# _detect_degeneration returns True without needing artifact_count.
_DEGEN_TEXT = "답변입니다 " * 80
_CLEAN_TEXT = (
    "안녕하세요. 요청하신 작업을 완료했습니다. 결과를 요약하면, 세 가지 "
    "핵심 변경이 있었고 각각 검증을 마쳤습니다. 추가로 필요한 부분이 있으면 "
    "말씀해 주세요. 감사합니다. 이상으로 최종 답변을 마칩니다."
)


class _FakeReq:
    """Placeholder returned by the stubbed _build_request (never actually sent)."""

    def __init__(self, model):
        self.model = model


def _install_sse_script(monkeypatch, round_texts):
    """Stub _stream_sse so each invocation drains one entry from round_texts into token_q.

    round_texts: list[str] — text emitted for round 0, 1, ... (no tool calls ever).
    Returns a dict tracking how many rounds ran and which model each round used.
    """
    state = {"round": 0, "models_seen": []}

    def _fake_stream_sse(req, token_q, tool_q, kind, stats, loop, reasoning_q=None):
        idx = state["round"]
        state["round"] += 1
        state["models_seen"].append(getattr(req, "model", None))
        text = round_texts[idx] if idx < len(round_texts) else _CLEAN_TEXT
        # Emit the whole round text as one token, then sentinels (no tools).
        loop.call_soon_threadsafe(token_q.put_nowait, text)
        loop.call_soon_threadsafe(token_q.put_nowait, None)
        loop.call_soon_threadsafe(tool_q.put_nowait, None)

    monkeypatch.setattr(s, "_stream_sse", _fake_stream_sse)
    return state


def _install_build_request(monkeypatch):
    """Stub _build_request to avoid real provider wiring; echoes model_override into req."""

    def _fake_build_request(input_items, system, ce_mode=False, research_mode=False,
                            tier=None, model_override=None, load=None):
        return _FakeReq(model_override), "chat_completions"

    monkeypatch.setattr(s, "_build_request", _fake_build_request)


async def _run(monkeypatch, round_texts, *, sturdier="anthropic/claude-sonnet"):
    _install_build_request(monkeypatch)
    state = _install_sse_script(monkeypatch, round_texts)
    # Pin the initial auto_route pick (resolve_turn_model) so the first round runs on flash;
    # stream_gpt does `from pipeline.model_catalog import resolve_turn_model` at startup to set
    # model_override — patch at the definition module (where-used = the import target).
    import pipeline.model_catalog as _mc
    monkeypatch.setattr(_mc, "resolve_turn_model",
                        lambda load: "deepseek/deepseek-v4-flash")
    # auto_route active + a distinct sturdier model available (or None to simulate manual sel).
    monkeypatch.setattr(s, "_sturdier_model",
                        lambda current: sturdier if sturdier != current else None)
    # Keep dynamic preamble / persona / build stable and side-effect free.
    monkeypatch.setattr(s, "build_dynamic_preamble", lambda: "")

    tokens: list[str] = []

    async def on_token(t):
        tokens.append(t)

    stats: dict = {"model": "deepseek/deepseek-v4-flash"}
    result = await s.stream_gpt(
        messages=[{"role": "user", "content": "hi"}],
        system="SYS",
        on_token=on_token,
        stats=stats,
    )
    return result, stats, tokens, state


@pytest.mark.asyncio
async def test_degenerate_final_round_regenerates_with_sturdier():
    """Degen final round → strip emitted text, switch model, record stats, regenerate clean."""
    import _pytest.monkeypatch as _mp
    mp = _mp.MonkeyPatch()
    try:
        result, stats, tokens, state = await _run(mp, [_DEGEN_TEXT, _CLEAN_TEXT])
    finally:
        mp.undo()

    # Two rounds ran: degen round, then regeneration round.
    assert state["round"] == 2
    # Second round used the sturdier model_override.
    assert state["models_seen"][0] == "deepseek/deepseek-v4-flash"  # initial auto pick
    assert state["models_seen"][1] == "anthropic/claude-sonnet"  # switched to sturdier
    # full_text (return) has the degen delta stripped and only the clean answer remains.
    assert _DEGEN_TEXT not in result
    assert result == _CLEAN_TEXT
    # Stats recorded the final-round degeneration.
    assert stats["degenerated"] is True
    assert stats["degen_final_round"] is True
    assert stats["degen_switched_to"] == "anthropic/claude-sonnet"
    assert any(r.get("final") for r in stats["degen_rounds"])


@pytest.mark.asyncio
async def test_clean_final_round_no_regeneration():
    """Clean final answer → single round, break, no degen stats."""
    import _pytest.monkeypatch as _mp
    mp = _mp.MonkeyPatch()
    try:
        result, stats, tokens, state = await _run(mp, [_CLEAN_TEXT])
    finally:
        mp.undo()

    assert state["round"] == 1  # broke immediately, no regeneration
    assert result == _CLEAN_TEXT
    assert "degenerated" not in stats
    assert "degen_final_round" not in stats


@pytest.mark.asyncio
async def test_degen_final_round_no_autoroute_streams_as_is():
    """Degen but _sturdier_model is None (manual selection) → no switch, break with text.

    Without an available sturdier model we do not loop forever; the degen text is kept
    (penalty is the primary fix; this branch only activates under auto_route)."""
    import _pytest.monkeypatch as _mp
    mp = _mp.MonkeyPatch()
    try:
        result, stats, tokens, state = await _run(mp, [_DEGEN_TEXT], sturdier=None)
    finally:
        mp.undo()

    assert state["round"] == 1  # no regeneration when no sturdier model
    assert result == _DEGEN_TEXT  # kept as-is (no rollback without switch)
    assert "degen_final_round" not in stats


@pytest.mark.asyncio
async def test_degen_retry_cap_respected():
    """If every round degenerates, we retry at most _DEGEN_MAX_RETRIES then break.

    Rounds run: initial degen (retry 0<max → switch+continue) + one regeneration that is
    STILL degen (retry now == max → break). So exactly _DEGEN_MAX_RETRIES+1 rounds."""
    import _pytest.monkeypatch as _mp
    mp = _mp.MonkeyPatch()
    try:
        # Always degenerate.
        result, stats, tokens, state = await _run(mp, [_DEGEN_TEXT] * 10)
    finally:
        mp.undo()

    assert state["round"] == s._DEGEN_MAX_RETRIES + 1
    assert stats["degenerated"] is True
