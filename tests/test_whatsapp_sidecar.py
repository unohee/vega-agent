# Created: 2026-07-02
# Purpose: INT-2323 — GoWA sidecar opt-in lifecycle (start gate, already-serving skip,
#          binary checks, stop). No real process spawned (Popen mocked).

from __future__ import annotations

import pipeline.whatsapp_sidecar as sc


def _reset(monkeypatch, **env):
    sc._proc = None
    for k in ("VEGA_WHATSAPP_SIDECAR", "VEGA_WHATSAPP_GOWA_BIN", "VEGA_WHATSAPP_GOWA_PORT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_disabled_by_default(monkeypatch):
    _reset(monkeypatch)
    assert sc.is_enabled() is False
    assert sc.start()["started"] is False
    assert sc.start()["reason"] == "not opted in"


def test_enabled_flag_variants(monkeypatch):
    for val in ("1", "true", "YES", "on"):
        _reset(monkeypatch, VEGA_WHATSAPP_SIDECAR=val)
        assert sc.is_enabled() is True
    for val in ("0", "false", "", "no"):
        _reset(monkeypatch, VEGA_WHATSAPP_SIDECAR=val)
        assert sc.is_enabled() is False


def test_skip_when_already_serving(monkeypatch):
    _reset(monkeypatch, VEGA_WHATSAPP_SIDECAR="1")
    monkeypatch.setattr(sc, "_already_serving", lambda port, timeout=1.5: True)
    r = sc.start()
    assert r["started"] is False and "already serving" in r["reason"]


def test_binary_missing(monkeypatch, tmp_path):
    _reset(monkeypatch, VEGA_WHATSAPP_SIDECAR="1", VEGA_WHATSAPP_GOWA_BIN=str(tmp_path / "nope"))
    monkeypatch.setattr(sc, "_already_serving", lambda port, timeout=1.5: False)
    r = sc.start()
    assert r["started"] is False and "binary not found" in r["reason"]


def test_start_spawns_when_enabled(monkeypatch, tmp_path):
    binary = tmp_path / "whatsapp"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    _reset(monkeypatch, VEGA_WHATSAPP_SIDECAR="1", VEGA_WHATSAPP_GOWA_BIN=str(binary),
           VEGA_WHATSAPP_GOWA_PORT="3777")
    monkeypatch.setattr(sc, "_already_serving", lambda port, timeout=1.5: False)

    calls = {}

    class _FakeProc:
        pid = 4242
        def terminate(self): calls["terminated"] = True
        def wait(self, timeout=None): return 0
        def kill(self): calls["killed"] = True

    def fake_popen(args, **kw):
        calls["args"] = args
        return _FakeProc()

    monkeypatch.setattr(sc.subprocess, "Popen", fake_popen)
    r = sc.start()
    assert r["started"] is True and r["pid"] == 4242 and r["port"] == 3777
    # correct invocation: <binary> rest --port 3777
    assert calls["args"][0] == str(binary)
    assert calls["args"][1] == "rest" and "3777" in calls["args"]

    # stop terminates the tracked process
    sc.stop()
    assert calls.get("terminated") is True
    assert sc._proc is None


def test_stop_noop_when_not_started(monkeypatch):
    _reset(monkeypatch)
    sc.stop()  # must not raise
    assert sc._proc is None
