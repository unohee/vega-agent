# Created: 2026-06-20
# Purpose: heartbeat-owned Google freshness sync orchestration tests; no credentials/network.

from __future__ import annotations

import pipeline.heartbeat as heartbeat


def test_google_freshness_lock_held_skips_without_ingest():
    calls: list[str] = []
    source = heartbeat.GoogleFreshnessSource(
        name="gmail",
        ingest=lambda _token, _cursor: calls.append("ingest"),
    )

    assert heartbeat._GOOGLE_FRESHNESS_LOCK.acquire(blocking=False)
    try:
        result = heartbeat.run_google_freshness_sync(
            sources=(source,),
            token_getter=lambda: "token",
        )
    finally:
        heartbeat._GOOGLE_FRESHNESS_LOCK.release()

    assert result == {"ok": True, "skipped": "lock_held", "sources": []}
    assert calls == []


def test_google_freshness_auth_missing_skips_without_crashing():
    calls: list[str] = []
    source = heartbeat.GoogleFreshnessSource(
        name="gmail",
        ingest=lambda _token, _cursor: calls.append("ingest"),
    )

    result = heartbeat.run_google_freshness_sync(
        sources=(source,),
        token_getter=lambda: None,
    )

    assert result == {"ok": True, "skipped": "auth_missing", "sources": []}
    assert calls == []


def test_google_freshness_source_failure_does_not_block_later_sources():
    calls: list[str] = []
    advanced: list[str] = []

    def fail_ingest(_token: str, cursor: str) -> str:
        calls.append(f"fail:{cursor}")
        raise RuntimeError("boom")

    def ok_ingest(_token: str, cursor: str) -> str:
        calls.append(f"ok:{cursor}")
        return "next-ok"

    sources = (
        heartbeat.GoogleFreshnessSource(
            name="gmail",
            load_cursor=lambda: "old-fail",
            ingest=fail_ingest,
            advance_cursor=lambda cursor: advanced.append(f"fail:{cursor}"),
        ),
        heartbeat.GoogleFreshnessSource(
            name="calendar",
            load_cursor=lambda: "old-ok",
            ingest=ok_ingest,
            advance_cursor=lambda cursor: advanced.append(f"ok:{cursor}"),
        ),
    )

    result = heartbeat.run_google_freshness_sync(
        sources=sources,
        token_getter=lambda: "token",
    )

    assert calls == ["fail:old-fail", "ok:old-ok"]
    assert advanced == ["ok:next-ok"]
    assert result["ok"] is False
    assert result["sources"][0]["source"] == "gmail"
    assert result["sources"][0]["ok"] is False
    assert "boom" in result["sources"][0]["error"]
    assert result["sources"][1] == {"source": "calendar", "ok": True}
