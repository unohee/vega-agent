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

    Cursor advancement is intentionally left to the heartbeat owner after this function returns.
    """
    scan_started = _utc_now()
    since = _cursor_time(cursor)
    query = f"after:{int(since.timestamp())}"
    base = "gmail.googleapis.com/gmail/v1/users/me"
    data = _gapi(f"{base}/messages", account=account, params={"q": query, "maxResults": _MAX_ITEMS_PER_SOURCE})
    records: list[dict[str, Any]] = []
    seen_ids = _existing_record_ids("gmail")
    for message in data.get("messages", [])[:_MAX_ITEMS_PER_SOURCE]:
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
    count = _append_records("gmail", records)
    next_cursor: dict[str, Any] = {
        "syncedAt": _iso(scan_started),
        "queryAfter": query,
        "updatedMin": _iso(scan_started),
        "ingested": count,
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
    scan_started = _utc_now()
    base = "www.googleapis.com/calendar/v3/calendars/primary/events"
    records: list[dict[str, Any]] = []
    seen_ids = _existing_record_ids("calendar")
    page_token = None
    next_sync_token = None
    pages = 0
    use_sync_token = bool(cursor and cursor.get("syncToken"))
    while pages < 5 and len(records) < _MAX_ITEMS_PER_SOURCE:
        params: dict[str, Any] = {"maxResults": min(250, _MAX_ITEMS_PER_SOURCE), "showDeleted": True}
        if use_sync_token:
            params["syncToken"] = cursor["syncToken"]  # type: ignore[index]
        else:
            params["updatedMin"] = _iso(_cursor_time(cursor))
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
            if len(records) >= _MAX_ITEMS_PER_SOURCE:
                break
        next_sync_token = data.get("nextSyncToken") or next_sync_token
        page_token = data.get("nextPageToken")
        pages += 1
        if not page_token:
            break
    count = _append_records("calendar", records)
    next_cursor = {"syncedAt": _iso(scan_started), "ingested": count}
    if next_sync_token:
        next_cursor["syncToken"] = next_sync_token
    return next_cursor


def ingest_drive_incremental(cursor: dict[str, Any] | None, account: str = "") -> dict[str, Any]:
    scan_started = _utc_now()
    since = _cursor_time(cursor)
    query = f"modifiedTime > '{_iso(since)}' and trashed = false"
    records: list[dict[str, Any]] = []
    page_token = None
    pages = 0
    while pages < 5 and len(records) < _MAX_ITEMS_PER_SOURCE:
        params: dict[str, Any] = {
            "q": query,
            "pageSize": min(100, _MAX_ITEMS_PER_SOURCE),
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,owners(emailAddress,displayName))",
        }
        if page_token:
            params["pageToken"] = page_token
        data = _gapi("www.googleapis.com/drive/v3/files", account=account, params=params)
        for file in data.get("files", []):
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
            if len(records) >= _MAX_ITEMS_PER_SOURCE:
                break
        page_token = data.get("nextPageToken")
        pages += 1
        if not page_token:
            break
    count = _append_records("drive", records)
    return {"syncedAt": _iso(scan_started), "query": query, "ingested": count}
