# Created: 2026-07-01
# Purpose: Regression tests for INT-2247 — Google freshness cursor pagination.
#          cap-truncated runs must NOT advance the watermark (permanent data loss)
#          and the next run must resume the same window via a continuation token.

from __future__ import annotations

import json
from datetime import datetime, timezone

import pipeline.heartbeat as heartbeat


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


_START = _iso(datetime(2026, 1, 1, tzinfo=timezone.utc))


# ---------------------------------------------------------------------------
# Gmail — page continuation across runs
# ---------------------------------------------------------------------------

def _gmail_two_page_gapi():
    """messages listing paginated into two pages of one message each."""
    base = "gmail.googleapis.com/gmail/v1/users/me"
    details = {
        "M1": {"id": "M1", "threadId": "T1", "internalDate": "1", "payload": {"headers": []}},
        "M2": {"id": "M2", "threadId": "T2", "internalDate": "2", "payload": {"headers": []}},
    }

    def gapi(path, account="", params=None, **kw):
        params = params or {}
        if path == f"{base}/messages":
            if params.get("pageToken") == "PAGE2":
                return {"messages": [{"id": "M2"}]}  # last page, no nextPageToken
            return {"messages": [{"id": "M1"}], "nextPageToken": "PAGE2"}
        if path == f"{base}/profile":
            return {"historyId": "H9"}
        for mid, detail in details.items():
            if path == f"{base}/messages/{mid}":
                return detail
        raise AssertionError(f"unexpected path {path} params={params}")

    return gapi


def test_gmail_cap_truncated_run_holds_watermark(tmp_path, monkeypatch):
    """First run hits the page budget: it must carry a continuation token and
    keep the watermark where it was (advancing it would permanently drop M2)."""
    from pipeline import google_freshness as gf

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", _gmail_two_page_gapi())
    monkeypatch.setattr(gf, "_MAX_PAGES_PER_RUN", 1)

    cursor = {"syncedAt": _START}
    first = gf.ingest_gmail_incremental(cursor, account="")

    assert first["complete"] is False
    assert first["pageToken"] == "PAGE2"
    # Watermark held — NOT advanced to scan time (this is the data-loss guard).
    assert first["syncedAt"] == _START
    assert first["ingested"] == 1
    ids = {json.loads(l)["id"] for l in (tmp_path / "gmail.jsonl").read_text().splitlines()}
    assert ids == {"M1"}


def test_gmail_next_run_resumes_and_drains(tmp_path, monkeypatch):
    """Feeding the continuation cursor back in fetches the remaining page and
    only then advances the watermark."""
    from pipeline import google_freshness as gf

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", _gmail_two_page_gapi())
    monkeypatch.setattr(gf, "_MAX_PAGES_PER_RUN", 1)

    first = gf.ingest_gmail_incremental({"syncedAt": _START}, account="")
    second = gf.ingest_gmail_incremental(first, account="")

    assert second["complete"] is True
    assert "pageToken" not in second
    # Watermark advances to the chain start (pendingSyncedAt carried from run 1).
    assert second["syncedAt"] == first["pendingSyncedAt"]
    ids = {json.loads(l)["id"] for l in (tmp_path / "gmail.jsonl").read_text().splitlines()}
    assert ids == {"M1", "M2"}  # nothing dropped across the two runs


def test_gmail_single_page_is_complete(tmp_path, monkeypatch):
    """A window that fits in one page drains immediately and advances."""
    from pipeline import google_freshness as gf

    base = "gmail.googleapis.com/gmail/v1/users/me"

    def gapi(path, account="", params=None, **kw):
        if path == f"{base}/messages":
            return {"messages": [{"id": "S1"}]}  # no nextPageToken
        if path == f"{base}/messages/S1":
            return {"id": "S1", "payload": {"headers": []}}
        if path == f"{base}/profile":
            return {"historyId": "H1"}
        raise AssertionError(path)

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", gapi)

    result = gf.ingest_gmail_incremental({"syncedAt": _START}, account="")
    assert result["complete"] is True
    assert result["syncedAt"] != _START  # advanced past the old watermark


# ---------------------------------------------------------------------------
# Calendar — continuation holds watermark + syncToken until drained
# ---------------------------------------------------------------------------

def test_calendar_cap_truncated_holds_watermark(tmp_path, monkeypatch):
    from pipeline import google_freshness as gf

    def gapi(path, account="", params=None, **kw):
        params = params or {}
        if params.get("pageToken") == "CPAGE2":
            return {"items": [{"id": "E2", "summary": "b"}], "nextSyncToken": "sync-final"}
        return {"items": [{"id": "E1", "summary": "a"}], "nextPageToken": "CPAGE2"}

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", gapi)
    monkeypatch.setattr(gf, "_MAX_PAGES_PER_RUN", 1)

    first = gf.ingest_calendar_incremental({"syncedAt": _START}, account="")
    assert first["complete"] is False
    assert first["pageToken"] == "CPAGE2"
    assert first["syncedAt"] == _START  # held
    assert "syncToken" not in first  # not committed until drained

    second = gf.ingest_calendar_incremental(first, account="")
    assert second["complete"] is True
    assert second.get("syncToken") == "sync-final"
    assert second["syncedAt"] == first["pendingSyncedAt"]
    ids = {json.loads(l)["id"] for l in (tmp_path / "calendar.jsonl").read_text().splitlines()}
    assert ids == {"E1", "E2"}


# ---------------------------------------------------------------------------
# Drive — orderBy determinism + dedup + continuation
# ---------------------------------------------------------------------------

def test_drive_requests_ordered_and_dedupes(tmp_path, monkeypatch):
    from pipeline import google_freshness as gf

    (tmp_path / "drive.jsonl").write_text(json.dumps({"id": "D_EXIST", "source": "drive"}) + "\n")
    seen_params: list[dict] = []

    def gapi(path, account="", params=None, **kw):
        seen_params.append(params or {})
        return {"files": [{"id": "D_EXIST", "name": "old"}, {"id": "D_NEW", "name": "new"}]}

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", gapi)

    result = gf.ingest_drive_incremental({"syncedAt": _START}, account="")
    assert seen_params[0].get("orderBy") == "modifiedTime"  # deterministic paging
    assert result["ingested"] == 1  # D_EXIST deduped
    assert result["complete"] is True
    ids = {json.loads(l)["id"] for l in (tmp_path / "drive.jsonl").read_text().splitlines()}
    assert ids == {"D_EXIST", "D_NEW"}


def test_drive_cap_truncated_holds_watermark(tmp_path, monkeypatch):
    from pipeline import google_freshness as gf

    def gapi(path, account="", params=None, **kw):
        params = params or {}
        if params.get("pageToken") == "DPAGE2":
            return {"files": [{"id": "D2", "name": "b"}]}
        return {"files": [{"id": "D1", "name": "a"}], "nextPageToken": "DPAGE2"}

    monkeypatch.setattr(gf, "_freshness_dir", lambda: tmp_path)
    monkeypatch.setattr(gf, "_gapi", gapi)
    monkeypatch.setattr(gf, "_MAX_PAGES_PER_RUN", 1)

    first = gf.ingest_drive_incremental({"syncedAt": _START}, account="")
    assert first["complete"] is False
    assert first["pageToken"] == "DPAGE2"
    assert first["syncedAt"] == _START  # held
    # Continuation reuses the original query the pageToken was issued against.
    second = gf.ingest_drive_incremental(first, account="")
    assert second["complete"] is True
    assert second["query"] == first["query"]
    assert second["syncedAt"] == first["pendingSyncedAt"]
    ids = {json.loads(l)["id"] for l in (tmp_path / "drive.jsonl").read_text().splitlines()}
    assert ids == {"D1", "D2"}


# ---------------------------------------------------------------------------
# heartbeat orchestrator — drains continuations in one run, guards loops
# ---------------------------------------------------------------------------

def test_heartbeat_drains_continuation_in_one_run(monkeypatch, tmp_path):
    """complete=False cursors are re-ingested in the same run until drained."""
    writes: list[dict] = []
    responses = [
        {"syncedAt": _START, "pageToken": "P1", "complete": False},
        {"syncedAt": _START, "pageToken": "P2", "complete": False},
        {"syncedAt": "2026-02-01", "complete": True},
    ]
    calls = {"n": 0}

    def ingest(cursor):
        r = responses[calls["n"]]
        calls["n"] += 1
        return r

    source = heartbeat._GoogleSource(
        name="gmail",
        read_cursor=lambda name: None,
        write_cursor=lambda name, cur: writes.append(cur),
        ingest=ingest,
    )
    monkeypatch.setattr(heartbeat, "_check_google_auth_for_heartbeat", lambda: True)
    monkeypatch.setattr(heartbeat, "_google_sources", lambda: [source])
    monkeypatch.setattr(heartbeat, "_GOOGLE_SYNC_DELAY_SEC", 0)
    monkeypatch.setattr(heartbeat, "_google_sync_lock_path", lambda: tmp_path / "t.lock")

    heartbeat.heartbeat_google_incremental_sync(force=True)

    assert calls["n"] == 3  # drained across three ingest calls
    assert writes[-1]["complete"] is True


def test_heartbeat_stops_on_non_advancing_token(monkeypatch, tmp_path):
    """A stuck continuation token must not loop forever."""
    writes: list[dict] = []
    calls = {"n": 0}

    def ingest(cursor):
        calls["n"] += 1
        return {"syncedAt": _START, "pageToken": "STUCK", "complete": False}

    source = heartbeat._GoogleSource(
        name="drive",
        read_cursor=lambda name: {"pageToken": "STUCK", "syncedAt": _START},
        write_cursor=lambda name, cur: writes.append(cur),
        ingest=ingest,
    )
    monkeypatch.setattr(heartbeat, "_check_google_auth_for_heartbeat", lambda: True)
    monkeypatch.setattr(heartbeat, "_google_sources", lambda: [source])
    monkeypatch.setattr(heartbeat, "_GOOGLE_SYNC_DELAY_SEC", 0)
    monkeypatch.setattr(heartbeat, "_google_sync_lock_path", lambda: tmp_path / "t.lock")

    heartbeat.heartbeat_google_incremental_sync(force=True)

    # Token never changes from the incoming "STUCK" → one ingest, then stop.
    assert calls["n"] == 1
