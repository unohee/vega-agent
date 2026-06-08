# Created: 2026-06-08
# Purpose: Permission consent gate integration test — stream_gpt on_consent (INT-1386)
# Dependencies: pytest, pytest-asyncio, pipeline.streaming

from __future__ import annotations

import asyncio
import urllib.request

import pytest

from pipeline import streaming


def _consent_sse(tool_name):
    """First round requests one dangerous tool, then ends with empty rounds."""
    state = {"called": False}
    def fake(req, token_q, tool_q, kind="responses", stats_out=None, loop=None):
        if not state["called"]:
            state["called"] = True
            tool_q.put_nowait({"name": tool_name, "arguments": "{}", "id": "fc1", "call_id": "c1"})
        token_q.put_nowait(None)
        tool_q.put_nowait(None)
    return fake


def _patch_common(monkeypatch):
    monkeypatch.setattr(streaming, "build_dynamic_preamble", lambda: "")
    monkeypatch.setattr(
        streaming, "_build_request",
        lambda *a, **k: (urllib.request.Request("http://x/responses", data=b"{}"), "responses"),
    )


@pytest.mark.asyncio
async def test_consent_denied_skips_dispatch(monkeypatch):
    """Denying consent for a dangerous tool (gmail_send) must skip dispatch_tool."""
    monkeypatch.setattr(streaming, "_stream_sse", _consent_sse("gmail_send"))
    _patch_common(monkeypatch)
    dispatched = []
    monkeypatch.setattr(streaming, "dispatch_tool", lambda n, a: dispatched.append(n) or '{"ok": true}')
    done = []
    async def on_token(t): pass
    async def on_tool_start(n, a, c): pass
    async def on_tool_done(n, r, c, **kw): done.append((n, r))
    async def on_consent(n, a, c): return False
    await asyncio.wait_for(streaming.stream_gpt(
        messages=[{"role": "user", "content": "send mail"}], system="sys",
        on_token=on_token, on_tool_start=on_tool_start,
        on_tool_done=on_tool_done, on_consent=on_consent,
    ), timeout=10.0)
    assert dispatched == [], "dispatch ran despite denial"
    assert done and "denied" in done[0][1], "denial result not passed to tool_done"


@pytest.mark.asyncio
async def test_consent_granted_runs_dispatch(monkeypatch):
    """Granting consent runs dispatch_tool normally."""
    monkeypatch.setattr(streaming, "_stream_sse", _consent_sse("gmail_send"))
    _patch_common(monkeypatch)
    dispatched = []
    monkeypatch.setattr(streaming, "dispatch_tool", lambda n, a: dispatched.append(n) or '{"ok": true}')
    async def on_token(t): pass
    async def on_tool_start(n, a, c): pass
    async def on_tool_done(n, r, c, **kw): pass
    async def on_consent(n, a, c): return True
    await asyncio.wait_for(streaming.stream_gpt(
        messages=[{"role": "user", "content": "send mail"}], system="sys",
        on_token=on_token, on_tool_start=on_tool_start,
        on_tool_done=on_tool_done, on_consent=on_consent,
    ), timeout=10.0)
    assert dispatched == ["gmail_send"]


@pytest.mark.asyncio
async def test_consent_not_required_for_safe_tool(monkeypatch):
    """Read-level tools (web_search) bypass the consent gate (on_consent not called)."""
    monkeypatch.setattr(streaming, "_stream_sse", _consent_sse("web_search"))
    _patch_common(monkeypatch)
    monkeypatch.setattr(streaming, "dispatch_tool", lambda n, a: '{"ok": true}')
    consent_calls = []
    async def on_token(t): pass
    async def on_tool_start(n, a, c): pass
    async def on_tool_done(n, r, c, **kw): pass
    async def on_consent(n, a, c): consent_calls.append(n); return True
    await asyncio.wait_for(streaming.stream_gpt(
        messages=[{"role": "user", "content": "search"}], system="sys",
        on_token=on_token, on_tool_start=on_tool_start,
        on_tool_done=on_tool_done, on_consent=on_consent,
    ), timeout=10.0)
    assert consent_calls == []
