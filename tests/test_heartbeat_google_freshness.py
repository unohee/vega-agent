# Created: 2026-06-20
# Purpose: heartbeat-owned Google incremental freshness sync orchestration tests; no credentials/network.

from __future__ import annotations

import sys
import types

import pipeline.heartbeat as heartbeat


def test_google_incremental_sync_lock_held_skips_without_ingest(monkeypatch):
    """When the thread lock is already held, sync skips without touching auth or sources."""
    calls: list[str] = []
    monkeypatch.setattr(heartbeat, "_check_google_auth_for_heartbeat", lambda: calls.append("auth") or True)
    monkeypatch.setattr(heartbeat, "_google_sources", lambda: calls.append("sources") or [])

    assert heartbeat._GOOGLE_SYNC_THREAD_LOCK.acquire(blocking=False)
    try:
        heartbeat.heartbeat_google_incremental_sync(force=True)
    finally:
        heartbeat._GOOGLE_SYNC_THREAD_LOCK.release()

    assert calls == []


def test_google_incremental_sync_auth_missing_skips_without_crashing(monkeypatch):
    """When auth check returns False, no sources are called."""
    calls: list[str] = []
    monkeypatch.setattr(heartbeat, "_check_google_auth_for_heartbeat", lambda: False)
    monkeypatch.setattr(heartbeat, "_google_sources", lambda: calls.append("sources") or [])

    heartbeat.heartbeat_google_incremental_sync(force=True)

    assert calls == []


def test_google_incremental_sync_default_auth_uses_get_access_token(monkeypatch):
    """Auth path imports get_access_token, not the removed ensure_valid_token symbol.

    Regression guard: the original ImportError was caused by importing the non-existent
    ensure_valid_token from pipeline.auth.google. This test provides only get_access_token
    on the mock module — any attempt to access ensure_valid_token would raise AttributeError.
    """
    google_auth = types.ModuleType("pipeline.auth.google")
    google_auth.stored_refresh_token = lambda: "refresh-tok"
    google_auth.get_access_token = lambda: "access-tok"
    monkeypatch.setitem(sys.modules, "pipeline.auth.google", google_auth)
    monkeypatch.setattr(heartbeat, "_google_sources", lambda: [])

    # Should complete without AttributeError or ImportError
    heartbeat.heartbeat_google_incremental_sync(force=True)


def test_google_incremental_sync_source_failure_does_not_block_later_sources(monkeypatch, tmp_path):
    """A failing source is logged and skipped; subsequent sources still run."""
    calls: list[str] = []
    advanced: list[str] = []

    def fail_ingest(cursor):
        calls.append("fail")
        raise RuntimeError("boom")

    def ok_ingest(cursor):
        calls.append("ok")
        return {"syncedAt": "2026-01-01"}

    sources = [
        heartbeat._GoogleSource(
            name="gmail",
            read_cursor=lambda name: None,
            write_cursor=lambda name, cur: advanced.append("fail-write"),
            ingest=fail_ingest,
        ),
        heartbeat._GoogleSource(
            name="calendar",
            read_cursor=lambda name: None,
            write_cursor=lambda name, cur: advanced.append("ok-write"),
            ingest=ok_ingest,
        ),
    ]

    monkeypatch.setattr(heartbeat, "_check_google_auth_for_heartbeat", lambda: True)
    monkeypatch.setattr(heartbeat, "_google_sources", lambda: sources)
    monkeypatch.setattr(heartbeat, "_GOOGLE_SYNC_DELAY_SEC", 0)
    monkeypatch.setattr(heartbeat, "_google_sync_lock_path", lambda: tmp_path / "test.lock")

    heartbeat.heartbeat_google_incremental_sync(force=True)

    assert calls == ["fail", "ok"]
    assert advanced == ["ok-write"]  # failing source cursor not advanced; ok source cursor is
