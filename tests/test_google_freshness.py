# Created: 2026-06-24
# Purpose: Unit tests for pipeline/google_freshness.py — 410 handling, Gmail dedup, Calendar dedup.

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# _existing_record_ids
# ---------------------------------------------------------------------------

def test_existing_record_ids_empty_file(tmp_path, monkeypatch):
    from pipeline import google_freshness as gf
    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    ids = gf._existing_record_ids("gmail")
    assert ids == set()


def test_existing_record_ids_reads_ids(tmp_path, monkeypatch):
    from pipeline import google_freshness as gf
    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    (tmp_path / "gmail.jsonl").write_text(
        json.dumps({"id": "abc"}) + "\n" + json.dumps({"id": "xyz"}) + "\n"
    )
    assert gf._existing_record_ids("gmail") == {"abc", "xyz"}


def test_existing_record_ids_skips_malformed_lines(tmp_path, monkeypatch):
    from pipeline import google_freshness as gf
    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    (tmp_path / "gmail.jsonl").write_text("not-json\n" + json.dumps({"id": "ok"}) + "\n")
    assert gf._existing_record_ids("gmail") == {"ok"}


# ---------------------------------------------------------------------------
# ingest_gmail_incremental — dedup
# ---------------------------------------------------------------------------

def test_ingest_gmail_dedup_skips_existing_id(tmp_path, monkeypatch):
    """Gmail ingest must not append a record whose id is already in the jsonl file."""
    from pipeline import google_freshness as gf

    existing_id = "MSG001"
    (tmp_path / "gmail.jsonl").write_text(json.dumps({"id": existing_id, "source": "gmail"}) + "\n")

    call_log: list[str] = []

    def mock_gapi(path: str, account: str = "", params: dict | None = None, **kw):
        call_log.append(path)
        if path.endswith("/profile"):
            return {"historyId": "H0"}
        if "messages" in path and "/" not in path.split("messages")[-1].lstrip("/"):
            # listing endpoint
            return {"messages": [{"id": existing_id}]}
        # detail endpoint — should NOT be reached for existing_id
        raise AssertionError(f"detail fetched for existing id: {path}")

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", mock_gapi)

    cursor = {"syncedAt": _iso(datetime(2026, 1, 1, tzinfo=timezone.utc))}
    gf.ingest_gmail_incremental(cursor, account="")

    lines = (tmp_path / "gmail.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1  # no new record appended


def test_ingest_gmail_appends_new_message(tmp_path, monkeypatch):
    """Gmail ingest appends a new message that wasn't in the file."""
    from pipeline import google_freshness as gf

    new_id = "MSG_NEW"

    def mock_gapi(path: str, account: str = "", params: dict | None = None, **kw):
        base = "gmail.googleapis.com/gmail/v1/users/me"
        if path == f"{base}/messages":
            return {"messages": [{"id": new_id}]}
        if path == f"{base}/messages/{new_id}":
            return {
                "id": new_id,
                "threadId": "TH1",
                "historyId": "H1",
                "internalDate": "1000",
                "snippet": "hi",
                "payload": {"headers": [{"name": "Subject", "value": "Test"}]},
            }
        if path == f"{base}/profile":
            return {"historyId": "H2"}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", mock_gapi)

    cursor = {"syncedAt": _iso(datetime(2026, 1, 1, tzinfo=timezone.utc))}
    result = gf.ingest_gmail_incremental(cursor, account="")

    assert result["ingested"] == 1
    lines = (tmp_path / "gmail.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["id"] == new_id


# ---------------------------------------------------------------------------
# ingest_calendar_incremental — dedup
# ---------------------------------------------------------------------------

def test_ingest_calendar_dedup_skips_existing_event(tmp_path, monkeypatch):
    """Calendar ingest must not append an event whose id is already in the jsonl file."""
    from pipeline import google_freshness as gf

    existing_id = "EVT001"
    (tmp_path / "calendar.jsonl").write_text(
        json.dumps({"id": existing_id, "source": "calendar"}) + "\n"
    )

    def mock_gapi(path, account="", params=None, **kw):
        return {
            "items": [{"id": existing_id, "summary": "Existing Event", "status": "confirmed"}],
            "nextSyncToken": "sync-tok-1",
        }

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", mock_gapi)

    cursor = {"syncedAt": _iso(datetime(2026, 1, 1, tzinfo=timezone.utc))}
    result = gf.ingest_calendar_incremental(cursor, account="")

    assert result["ingested"] == 0
    lines = (tmp_path / "calendar.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1


def test_ingest_calendar_appends_new_event(tmp_path, monkeypatch):
    """Calendar ingest appends a new event not already in the file."""
    from pipeline import google_freshness as gf

    new_id = "EVT_NEW"

    def mock_gapi(path, account="", params=None, **kw):
        return {
            "items": [{"id": new_id, "summary": "New Event", "status": "confirmed", "updated": "2026-01-01T00:00:00Z"}],
            "nextSyncToken": "sync-tok-new",
        }

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", mock_gapi)

    result = gf.ingest_calendar_incremental(None, account="")

    assert result["ingested"] == 1
    assert result.get("syncToken") == "sync-tok-new"
    lines = (tmp_path / "calendar.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == new_id


# ---------------------------------------------------------------------------
# ingest_calendar_incremental — 410 full-resync
# ---------------------------------------------------------------------------

def test_ingest_calendar_410_clears_sync_token_and_does_full_resync(tmp_path, monkeypatch):
    """On 410, calendar ingest falls back to updatedMin full-resync and returns a fresh syncToken."""
    from pipeline import google_freshness as gf

    call_count = [0]

    def mock_gapi(path, account="", params=None, **kw):
        call_count[0] += 1
        params = params or {}
        if "syncToken" in params:
            # First call — simulate 410 expiry
            raise RuntimeError("Google API HTTP 410: sync token expired")
        # Full-resync call
        assert "updatedMin" in params
        return {
            "items": [{"id": "EVT_RESYNC", "summary": "Resync Event", "status": "confirmed"}],
            "nextSyncToken": "fresh-sync-token",
        }

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", mock_gapi)

    cursor = {
        "syncedAt": _iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        "syncToken": "stale-token",
    }
    result = gf.ingest_calendar_incremental(cursor, account="")

    assert result["ingested"] == 1
    assert result.get("syncToken") == "fresh-sync-token"
    assert call_count[0] == 2  # one 410, one full-resync


def test_ingest_calendar_410_does_not_duplicate_pre_410_records(tmp_path, monkeypatch):
    """Records accumulated before 410 are discarded; full-resync deduplicates against the file."""
    from pipeline import google_freshness as gf

    existing_id = "EVT_EXISTING"
    (tmp_path / "calendar.jsonl").write_text(
        json.dumps({"id": existing_id, "source": "calendar"}) + "\n"
    )

    calls: list[str] = []

    def mock_gapi(path, account="", params=None, **kw):
        params = params or {}
        if "syncToken" in params:
            calls.append("410")
            raise RuntimeError("Google API HTTP 410: sync token expired")
        calls.append("resync")
        return {
            "items": [
                {"id": existing_id, "summary": "Already Have This"},  # should be deduped
                {"id": "EVT_FRESH", "summary": "New One"},
            ],
            "nextSyncToken": "new-token",
        }

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", mock_gapi)

    cursor = {
        "syncedAt": _iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        "syncToken": "stale-token",
    }
    result = gf.ingest_calendar_incremental(cursor, account="")

    assert result["ingested"] == 1  # only EVT_FRESH
    lines = (tmp_path / "calendar.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2  # existing + fresh
    ids = {json.loads(l)["id"] for l in lines}
    assert ids == {existing_id, "EVT_FRESH"}
    assert calls == ["410", "resync"]


def test_ingest_calendar_non_410_exception_propagates(tmp_path, monkeypatch):
    """Non-410 exceptions from the API must propagate, not be swallowed."""
    from pipeline import google_freshness as gf

    def mock_gapi(path, account="", params=None, **kw):
        raise RuntimeError("Google API HTTP 500: internal error")

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", mock_gapi)

    cursor = {
        "syncedAt": _iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        "syncToken": "tok",
    }
    with pytest.raises(RuntimeError, match="500"):
        gf.ingest_calendar_incremental(cursor, account="")
