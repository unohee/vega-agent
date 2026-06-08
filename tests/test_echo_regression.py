# Created: 2026-06-08
# Purpose: 멀티턴 응답 에코 방지 회귀 — 도구 라운드 텍스트 history 보존 (INT-1411)
# Dependencies: pytest, pytest-asyncio, pipeline.streaming

from __future__ import annotations

import asyncio
import urllib.request

import pytest

from pipeline import streaming


@pytest.mark.asyncio
async def test_round_text_preserved_in_history(monkeypatch):
    """A tool-calling round's assistant text must persist into the next round's
    input_items. If dropped, the model repeats the same intro (echo) — INT-1411."""
    rounds = {"n": 0}

    def fake_stream_sse(req, token_q, tool_q, kind="responses", stats_out=None, loop=None):
        rounds["n"] += 1
        if rounds["n"] == 1:
            for ch in "맞아. 검색해볼게.":
                token_q.put_nowait(ch)
            tool_q.put_nowait({"name": "web_search", "arguments": "{}", "id": "fc1", "call_id": "c1"})
        else:
            for ch in "결과 요약.":
                token_q.put_nowait(ch)
        token_q.put_nowait(None)
        tool_q.put_nowait(None)

    monkeypatch.setattr(streaming, "_stream_sse", fake_stream_sse)
    monkeypatch.setattr(streaming, "build_dynamic_preamble", lambda: "")
    monkeypatch.setattr(streaming, "dispatch_tool", lambda n, a: '{"ok": true}')

    captured = []
    def fake_build(input_items, system, **k):
        captured.append([dict(it) for it in input_items])
        return (urllib.request.Request("http://x/responses", data=b"{}"), "responses")
    monkeypatch.setattr(streaming, "_build_request", fake_build)

    async def on_token(t): pass
    async def on_tool_start(n, a, c): pass
    async def on_tool_done(n, r, c, **kw): pass

    await asyncio.wait_for(streaming.stream_gpt(
        messages=[{"role": "user", "content": "검색해"}], system="sys",
        on_token=on_token, on_tool_start=on_tool_start, on_tool_done=on_tool_done,
    ), timeout=10.0)

    assert len(captured) >= 2, "도구 루프가 2라운드 진입 안 함"
    second = captured[1]
    assistant_texts = [it.get("content") for it in second
                       if it.get("role") == "assistant" and isinstance(it.get("content"), str)]
    assert any("맞아. 검색해볼게." in t for t in assistant_texts), \
        f"1라운드 어시스턴트 텍스트 누락 — 에코 회귀. assistant: {assistant_texts}"
