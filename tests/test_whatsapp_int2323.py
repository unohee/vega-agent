# Created: 2026-07-02
# Purpose: WhatsApp native tools (GoWA REST wrapper) — unit tests (INT-2323)
# Dependencies: pipeline/tools_whatsapp.py, pipeline/tool_registry.py
# Test Status: green (2026-07-02)

from __future__ import annotations

import io
import json

import pytest

import pipeline.tools_whatsapp as wa


class _FakeResp:
    """Minimal context-manager stand-in for urllib.request.urlopen return value."""

    def __init__(self, payload: dict):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install_urlopen(monkeypatch, handler):
    """handler(req) -> dict payload. Also records the captured Request objects."""
    calls: list = []

    def _fake_urlopen(req, timeout=None, **kw):
        calls.append(req)
        return _FakeResp(handler(req))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    return calls


@pytest.fixture(autouse=True)
def _reset_cache():
    wa.reset_device_cache()
    yield
    wa.reset_device_cache()


_DEVICES = {"results": [{"name": "iPhone", "device": "dev-1", "jid": "111@s.whatsapp.net"}]}


# ── device resolution ─────────────────────────────────────────────────────────

class TestDeviceResolve:
    def test_resolves_and_caches(self, monkeypatch):
        hits = {"devices": 0}

        def handler(req):
            if req.full_url.endswith("/app/devices"):
                hits["devices"] += 1
                return _DEVICES
            raise AssertionError("unexpected url")

        _install_urlopen(monkeypatch, handler)
        assert wa._resolve_device() == "dev-1"
        # second call is served from cache — no extra /app/devices hit
        assert wa._resolve_device() == "dev-1"
        assert hits["devices"] == 1

    def test_no_device_raises(self, monkeypatch):
        _install_urlopen(monkeypatch, lambda req: {"results": []})
        with pytest.raises(RuntimeError, match="페어링"):
            wa._resolve_device()

    def test_has_paired_device_false_on_connection_error(self, monkeypatch):
        def _boom(req, timeout=None, **kw):
            raise OSError("connection refused")
        monkeypatch.setattr("urllib.request.urlopen", _boom)
        assert wa.has_paired_device() is False


# ── list_chats ────────────────────────────────────────────────────────────────

class TestListChats:
    def test_parses_results_data(self, monkeypatch):
        def handler(req):
            if req.full_url.endswith("/app/devices"):
                return _DEVICES
            if "/chats" in req.full_url:
                assert req.get_header("X-device-id") == "dev-1"
                return {"results": {"data": [
                    {"jid": "aaa@s.whatsapp.net", "name": "Alice",
                     "last_message_time": "2026-07-02T10:00:00Z", "archived": False},
                    {"jid": "grp@g.us", "name": "Team", "last_message_time": "x", "archived": True},
                ], "pagination": {"cursor": "c"}}}
            raise AssertionError(req.full_url)

        _install_urlopen(monkeypatch, handler)
        chats = wa.whatsapp_list_chats(limit=5)
        assert [c["jid"] for c in chats] == ["aaa@s.whatsapp.net", "grp@g.us"]
        assert chats[0]["name"] == "Alice"

    def test_defensive_direct_array_results(self, monkeypatch):
        # GoWA sometimes returns results as a plain array — must not crash
        def handler(req):
            if req.full_url.endswith("/app/devices"):
                return _DEVICES
            return {"results": [{"jid": "aaa@s.whatsapp.net", "name": "Alice"}]}

        _install_urlopen(monkeypatch, handler)
        chats = wa.whatsapp_list_chats()
        assert chats[0]["jid"] == "aaa@s.whatsapp.net"


# ── read_messages ─────────────────────────────────────────────────────────────

class TestReadMessages:
    def test_parses_messages(self, monkeypatch):
        def handler(req):
            if req.full_url.endswith("/app/devices"):
                return _DEVICES
            if "/messages" in req.full_url:
                assert "/chat/aaa@s.whatsapp.net/messages" in req.full_url
                return {"results": {"data": [
                    {"id": "m1", "sender": "Alice", "timestamp": "t1", "text": "hi"},
                    {"id": "m2", "from": "me", "message": {"conversation": "yo"}},
                ]}}
            raise AssertionError(req.full_url)

        _install_urlopen(monkeypatch, handler)
        msgs = wa.whatsapp_read_messages("aaa@s.whatsapp.net", limit=10)
        assert msgs[0]["id"] == "m1"
        assert msgs[0]["text"] == "hi"
        assert msgs[1]["text"] == "yo"  # nested dict extraction


# ── send_message ──────────────────────────────────────────────────────────────

class TestSendMessage:
    def test_auto_suffix_and_post_body(self, monkeypatch):
        captured = {}

        def handler(req):
            if req.full_url.endswith("/app/devices"):
                return _DEVICES
            if req.full_url.endswith("/send/message"):
                assert req.get_method() == "POST"
                assert req.get_header("X-device-id") == "dev-1"
                assert req.get_header("Content-type") == "application/json"
                captured["body"] = json.loads(req.data.decode())
                return {"code": "SUCCESS", "results": {"message_id": "M1", "status": "sent"}}
            raise AssertionError(req.full_url)

        _install_urlopen(monkeypatch, handler)
        res = wa.whatsapp_send_message("821012345678", "hello")
        assert captured["body"] == {"phone": "821012345678@s.whatsapp.net", "message": "hello"}
        assert res["ok"] is True
        assert res["phone"] == "821012345678@s.whatsapp.net"

    def test_group_jid_kept_as_is(self, monkeypatch):
        captured = {}

        def handler(req):
            if req.full_url.endswith("/app/devices"):
                return _DEVICES
            captured["body"] = json.loads(req.data.decode())
            return {"code": "SUCCESS", "results": {}}

        _install_urlopen(monkeypatch, handler)
        wa.whatsapp_send_message("grp@g.us", "hi team")
        assert captured["body"]["phone"] == "grp@g.us"  # no double suffix


# ── registry wiring ───────────────────────────────────────────────────────────

class TestRegistryWiring:
    def test_toolset_registered(self):
        import pipeline.tool_registry as reg
        assert "whatsapp" in reg.WORKSPACE_TOOLSETS
        for t in ("whatsapp_list_chats", "whatsapp_read_messages", "whatsapp_send_message"):
            assert reg.toolset_of(t) == "whatsapp"

    def test_schemas_and_functions_exposed(self):
        import pipeline.tools as tools
        names = {s["name"] for s in tools.TOOL_SCHEMAS}
        for t in ("whatsapp_list_chats", "whatsapp_read_messages", "whatsapp_send_message"):
            assert t in names
            assert t in tools.TOOL_FUNCTIONS

    def test_check_fn_gates_on_pairing(self, monkeypatch):
        import pipeline.tool_registry as reg
        reg.invalidate_check_fn_cache()
        monkeypatch.setattr(wa, "has_paired_device", lambda timeout=3.0: False)
        assert reg.is_toolset_available("whatsapp") is False
        reg.invalidate_check_fn_cache()
        monkeypatch.setattr(wa, "has_paired_device", lambda timeout=3.0: True)
        assert reg.is_toolset_available("whatsapp") is True
