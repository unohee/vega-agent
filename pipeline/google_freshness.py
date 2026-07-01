# Created: 2026-06-20
# Purpose: Google freshness cursors and incremental ingest entrypoints for heartbeat orchestration.
# Dependencies: pipeline.data_paths, pipeline.tools_google

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_VALID_SOURCES = {"gmail", "calendar", "drive"}
_DEFAULT_LOOKBACK_DAYS = int(os.environ.get("VEGA_GOOGLE_FRESHNESS_LOOKBACK_DAYS", "7"))
_MAX_ITEMS_PER_SOURCE = int(os.environ.get("VEGA_GOOGLE_FRESHNESS_MAX_ITEMS", "50"))
# Per-run page budget. Each ingest fetches at most this many API pages; if more
# remain (drain incomplete) it stores a continuation token instead of advancing
# the watermark, so the heartbeat resumes the same window on the next call.
_MAX_PAGES_PER_RUN = int(os.environ.get("VEGA_GOOGLE_FRESHNESS_MAX_PAGES", "5"))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _freshness_dir() -> Path:
    try:
        from pipeline.data_paths import data_dir

        base = Path(data_dir())
    except Exception:
        base = Path(os.environ.get("VEGA_DATA_DIR", ".vega"))
    path = base / "google_freshness"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cursor_file() -> Path:
    return _freshness_dir() / "cursors.json"


def _records_file(source: str) -> Path:
    _validate_source(source)
    return _freshness_dir() / f"{source}.jsonl"


def _validate_source(source: str) -> None:
    if source not in _VALID_SOURCES:
        raise ValueError(f"unknown Google freshness source: {source}")


def _load_cursors() -> dict[str, Any]:
    path = _cursor_file()
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"Google freshness cursor file is not an object: {path}")
    return data


def _store_cursors(cursors: dict[str, Any]) -> None:
    path = _cursor_file()
    fd, tmp = tempfile.mkstemp(prefix="cursors.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cursors, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_google_freshness_cursor(source: str) -> dict[str, Any] | None:
    _validate_source(source)
    cursor = _load_cursors().get(source)
    if cursor is None:
        return None
    if not isinstance(cursor, dict):
        raise RuntimeError(f"Google freshness cursor for {source} is not an object")
    return cursor


def write_google_freshness_cursor(source: str, cursor: dict[str, Any]) -> None:
    _validate_source(source)
    if not isinstance(cursor, dict) or not cursor.get("syncedAt"):
        raise ValueError(f"invalid Google freshness cursor for {source}: {cursor!r}")
    cursors = _load_cursors()
    cursors[source] = cursor
    _store_cursors(cursors)


def _cursor_time(cursor: dict[str, Any] | None) -> datetime:
    if cursor:
        parsed = _parse_iso(str(cursor.get("syncedAt") or cursor.get("updatedMin") or ""))
        if parsed:
            return parsed
    return _utc_now() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)


def _append_records(source: str, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    path = _records_file(source)
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
    return len(records)


def _existing_record_ids(source: str) -> set[str]:
    path = _records_file(source)
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record_id = record.get("id") if isinstance(record, dict) else None
            if record_id:
                ids.add(str(record_id))
    return ids


def _google_api_status(exc: Exception) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "resp", None)
    value = getattr(response, "status", None)
    if isinstance(value, int):
        return value
    text = str(exc)
    marker = "Google API HTTP "
    if marker not in text:
        return None
    tail = text.split(marker, 1)[1]
    try:
        return int(tail.split(":", 1)[0].strip())
    except ValueError:
        return None


def _gapi(path: str, account: str = "", params: dict | None = None, method: str = "GET", body: dict | None = None) -> dict:
    from pipeline.tools_google import _gapi as google_api

    return google_api(path, account=account, params=params, method=method, body=body)


def ingest_gmail_incremental(cursor: dict[str, Any] | None, account: str = "") -> dict[str, Any]:
    """Fetch Gmail changes since the supplied heartbeat cursor and persist freshness records.

    Paginates through the ``after:`` window instead of taking only the first page.
    Because Gmail lists newest-first, a single page would permanently drop older
    changes once the window exceeds one page. When more pages remain than the
    per-run budget allows, the returned cursor keeps the current watermark and
    carries a ``pageToken`` (``complete: False``) so the heartbeat resumes the
    same window; the watermark only advances once the window is fully drained.

    Cursor advancement is intentionally left to the heartbeat owner after this function returns.
    """
    scan_started = _utc_now()
    base = "gmail.googleapis.com/gmail/v1/users/me"
    continuing = bool(cursor and cursor.get("pageToken"))
    if continuing:
        # Resume the in-progress window: reuse the original query the pageToken
        # was issued against and hold the watermark where it was.
        query = str(cursor.get("queryAfter") or f"after:{int(_cursor_time(cursor).timestamp())}")
        since_iso = str(cursor.get("syncedAt") or _iso(_cursor_time(cursor)))
        pending_synced = str(cursor.get("pendingSyncedAt") or since_iso)
        page_token: str | None = str(cursor["pageToken"])  # type: ignore[index]
    else:
        since = _cursor_time(cursor)
        query = f"after:{int(since.timestamp())}"
        since_iso = _iso(since)
        pending_synced = _iso(scan_started)
        page_token = None

    records: list[dict[str, Any]] = []
    seen_ids = _existing_record_ids("gmail")
    next_page_token: str | None = None
    pages = 0
    while pages < _MAX_PAGES_PER_RUN and len(records) < _MAX_ITEMS_PER_SOURCE:
        params: dict[str, Any] = {"q": query, "maxResults": _MAX_ITEMS_PER_SOURCE}
        if page_token:
            params["pageToken"] = page_token
        data = _gapi(f"{base}/messages", account=account, params=params)
        for message in data.get("messages", []):
            message_id = message.get("id")
            if not message_id or str(message_id) in seen_ids:
                continue
            detail = _gapi(
                f"{base}/messages/{message_id}",
                account=account,
                params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
            )
            headers = {h.get("name", ""): h.get("value", "") for h in detail.get("payload", {}).get("headers", [])}
            record_id = str(detail.get("id", message_id))
            if record_id in seen_ids:
                continue
            seen_ids.add(record_id)
            records.append({
                "source": "gmail",
                "id": record_id,
                "threadId": detail.get("threadId", message.get("threadId", "")),
                "historyId": detail.get("historyId", ""),
                "internalDate": detail.get("internalDate", ""),
                "snippet": detail.get("snippet", ""),
                "headers": headers,
                "ingestedAt": _iso(_utc_now()),
            })
        next_page_token = data.get("nextPageToken")
        page_token = next_page_token
        pages += 1
        if not next_page_token:
            break
    count = _append_records("gmail", records)
    complete = not next_page_token

    if not complete:
        # More pages remain: hold the watermark, carry the continuation token.
        return {
            "syncedAt": since_iso,
            "queryAfter": query,
            "updatedMin": since_iso,
            "pageToken": next_page_token,
            "pendingSyncedAt": pending_synced,
            "ingested": count,
            "complete": False,
        }

    # Window drained: advance the watermark to the start of this scan chain.
    next_cursor: dict[str, Any] = {
        "syncedAt": pending_synced,
        "queryAfter": query,
        "updatedMin": pending_synced,
        "ingested": count,
        "complete": True,
    }
    try:
        profile = _gapi(f"{base}/profile", account=account)
        if profile.get("historyId"):
            next_cursor["historyId"] = str(profile["historyId"])
    except Exception as exc:
        if count == 0:
            raise
        print(f"[google_freshness] gmail profile fetch warning: {exc}")
    return next_cursor


def ingest_calendar_incremental(cursor: dict[str, Any] | None, account: str = "") -> dict[str, Any]:
    """Fetch Calendar changes and persist freshness records.

    ``maxResults`` is bounded by the per-source cap so each page maps cleanly to
    the cap (no mid-page truncation, which would strand events the pageToken
    cannot recover). When the page budget is exhausted before the sync/page
    stream drains, the watermark is held and a ``pageToken`` continuation is
    returned; the watermark advances (and ``syncToken`` is committed) only once
    the stream is fully drained.
    """
    scan_started = _utc_now()
    base = "www.googleapis.com/calendar/v3/calendars/primary/events"
    records: list[dict[str, Any]] = []
    seen_ids = _existing_record_ids("calendar")
    continuing = bool(cursor and cursor.get("pageToken"))
    page_token: str | None = str(cursor["pageToken"]) if continuing else None  # type: ignore[index]
    next_sync_token = None
    pages = 0
    use_sync_token = bool(cursor and cursor.get("syncToken"))
    since_iso = _iso(_cursor_time(cursor))
    pending_synced = (
        str(cursor.get("pendingSyncedAt")) if (continuing and cursor and cursor.get("pendingSyncedAt")) else _iso(scan_started)
    )
    while pages < _MAX_PAGES_PER_RUN and len(records) < _MAX_ITEMS_PER_SOURCE:
        params: dict[str, Any] = {"maxResults": min(250, _MAX_ITEMS_PER_SOURCE), "showDeleted": True}
        if use_sync_token:
            params["syncToken"] = cursor["syncToken"]  # type: ignore[index]
        else:
            params["updatedMin"] = since_iso
        if page_token:
            params["pageToken"] = page_token
        try:
            data = _gapi(base, account=account, params=params)
        except Exception as exc:
            if use_sync_token and _google_api_status(exc) == 410:
                records.clear()
                seen_ids = _existing_record_ids("calendar")
                page_token = None
                next_sync_token = None
                pages = 0
                use_sync_token = False
                continue
            raise
        for event in data.get("items", []):
            event_id = event.get("id", "")
            if event_id and str(event_id) in seen_ids:
                continue
            seen_ids.add(str(event_id))
            records.append({
                "source": "calendar",
                "id": event_id,
                "status": event.get("status", ""),
                "summary": event.get("summary", ""),
                "updated": event.get("updated", ""),
                "start": event.get("start", {}),
                "end": event.get("end", {}),
                "htmlLink": event.get("htmlLink", ""),
                "ingestedAt": _iso(_utc_now()),
            })
        next_sync_token = data.get("nextSyncToken") or next_sync_token
        page_token = data.get("nextPageToken")
        pages += 1
        if not page_token:
            break
    count = _append_records("calendar", records)
    complete = not page_token

    if not complete:
        # Page budget exhausted mid-stream: hold watermark, carry continuation.
        next_cursor: dict[str, Any] = {
            "syncedAt": since_iso,
            "pageToken": page_token,
            "pendingSyncedAt": pending_synced,
            "ingested": count,
            "complete": False,
        }
        if use_sync_token and cursor and cursor.get("syncToken"):
            next_cursor["syncToken"] = str(cursor["syncToken"])
        return next_cursor

    next_cursor = {"syncedAt": pending_synced, "ingested": count, "complete": True}
    if next_sync_token:
        next_cursor["syncToken"] = next_sync_token
    return next_cursor


def ingest_drive_incremental(cursor: dict[str, Any] | None, account: str = "") -> dict[str, Any]:
    """Fetch Drive changes and persist freshness records.

    Adds ``orderBy=modifiedTime`` so paging is deterministic (unordered results
    could non-deterministically drop files at the page/item cap) and dedupes
    against already-persisted ids. When the page budget is exhausted before the
    result set drains, the watermark is held and a ``pageToken`` continuation is
    returned so the next run resumes the same query window.
    """
    scan_started = _utc_now()
    since = _cursor_time(cursor)
    since_iso = _iso(since)
    continuing = bool(cursor and cursor.get("pageToken"))
    query = (
        str(cursor["query"]) if (continuing and cursor and cursor.get("query"))
        else f"modifiedTime > '{since_iso}' and trashed = false"
    )
    pending_synced = (
        str(cursor.get("pendingSyncedAt")) if (continuing and cursor and cursor.get("pendingSyncedAt")) else _iso(scan_started)
    )
    page_token: str | None = str(cursor["pageToken"]) if continuing else None  # type: ignore[index]
    records: list[dict[str, Any]] = []
    seen_ids = _existing_record_ids("drive")
    pages = 0
    while pages < _MAX_PAGES_PER_RUN and len(records) < _MAX_ITEMS_PER_SOURCE:
        params: dict[str, Any] = {
            "q": query,
            "pageSize": min(100, _MAX_ITEMS_PER_SOURCE),
            "orderBy": "modifiedTime",
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,owners(emailAddress,displayName))",
        }
        if page_token:
            params["pageToken"] = page_token
        data = _gapi("www.googleapis.com/drive/v3/files", account=account, params=params)
        for file in data.get("files", []):
            file_id = str(file.get("id", ""))
            if file_id and file_id in seen_ids:
                continue
            seen_ids.add(file_id)
            records.append({
                "source": "drive",
                "id": file.get("id", ""),
                "name": file.get("name", ""),
                "mimeType": file.get("mimeType", ""),
                "modifiedTime": file.get("modifiedTime", ""),
                "webViewLink": file.get("webViewLink", ""),
                "owners": file.get("owners", []),
                "ingestedAt": _iso(_utc_now()),
            })
        page_token = data.get("nextPageToken")
        pages += 1
        if not page_token:
            break
    count = _append_records("drive", records)
    complete = not page_token

    if not complete:
        return {
            "syncedAt": since_iso,
            "query": query,
            "pageToken": page_token,
            "pendingSyncedAt": pending_synced,
            "ingested": count,
            "complete": False,
        }
    return {"syncedAt": pending_synced, "query": query, "ingested": count, "complete": True}
