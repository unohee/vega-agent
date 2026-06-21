# Created: 2026-06-20
# Purpose: Compact external sync freshness/health collector.
# Dependencies: stdlib sqlite3, pipeline.data_paths, pipeline.auth.google

from __future__ import annotations

import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from pipeline.data_paths import db_path

SYNC_TABLES = ("google_sync_cursors", "extraction_checkpoints")
_CURSOR_COLUMNS = (
    "cursor_timestamp",
    "cursor_ts",
    "last_cursor_timestamp",
    "updated_at",
    "last_updated_at",
    "created_at",
)
_SOURCE_COLUMNS = ("source", "provider", "account", "email", "service", "name", "checkpoint", "key")
_SUCCESS_COLUMNS = ("last_success", "last_success_at", "last_success_ts", "success_at", "completed_at")
_ERROR_COLUMNS = ("last_error", "error", "error_message", "last_error_message")
_MAX_ROWS_PER_TABLE = 50


def sync_status() -> dict[str, Any]:
    """Return Google/extraction freshness state without mutating SQLite."""
    path = db_path()
    google = _google_status()
    result: dict[str, Any] = {
        "status": "ok",
        "db_path": str(path),
        "reauth_needed": bool(google.get("reauth_needed")),
        "google": google,
        "tables": {},
    }

    if not path.exists():
        return _degraded(result, "sync database unavailable: file does not exist", "database file does not exist")

    try:
        with _connect_readonly(path) as conn:
            conn.row_factory = sqlite3.Row
            missing = False
            for table in SYNC_TABLES:
                if not _table_exists(conn, table):
                    missing = True
                    result["tables"][table] = _unavailable_table("table does not exist")
                else:
                    result["tables"][table] = _table_status(conn, table)
            if missing:
                result["status"] = "degraded"
            return result
    except sqlite3.Error as exc:
        return _degraded(result, f"sync database unreadable: {exc}", "database unreadable")


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = "file:" + urllib.parse.quote(str(path), safe="/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _google_status() -> dict[str, Any]:
    try:
        from pipeline.auth import google

        configured = google.is_configured()
        authenticated = google.is_authenticated()
        return {
            "configured": configured,
            "authenticated": authenticated,
            "email": google.stored_email(),
            "accounts": google.stored_accounts(),
            "client_source": google.client_source(),
            "reauth_needed": bool(configured and not authenticated),
        }
    except Exception as exc:  # explicit degraded auth state; do not hide it from the response
        return {
            "configured": False,
            "authenticated": False,
            "email": None,
            "accounts": [],
            "client_source": "none",
            "reauth_needed": True,
            "error": str(exc),
        }


def _degraded(result: dict[str, Any], error: str, table_error: str) -> dict[str, Any]:
    result["status"] = "degraded"
    result["error"] = error
    for table in SYNC_TABLES:
        result["tables"].setdefault(table, _unavailable_table(table_error))
    return result


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_status(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    quoted = _quote_identifier(table)
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({quoted})").fetchall()]
    rows = conn.execute(f"SELECT * FROM {quoted} ORDER BY rowid DESC LIMIT ?", (_MAX_ROWS_PER_TABLE,)).fetchall()
    return {
        "available": True,
        "row_count": conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0],
        "columns": columns,
        "items": [_row_status(dict(row), columns) for row in rows],
    }


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _row_status(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    cursor_timestamps = {
        col: _json_value(row.get(col))
        for col in columns
        if _is_cursor_timestamp_column(col) and row.get(col) is not None
    }
    return {
        "source": _first_present(row, _SOURCE_COLUMNS),
        "last_success": _first_present(row, _SUCCESS_COLUMNS),
        "last_error": _first_present(row, _ERROR_COLUMNS),
        "cursor_timestamps": cursor_timestamps,
        "raw": {col: _json_value(row.get(col)) for col in columns},
    }


def _unavailable_table(reason: str) -> dict[str, Any]:
    return {"available": False, "row_count": 0, "columns": [], "items": [], "error": reason}


def _first_present(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    for key in candidates:
        if key in row and row[key] is not None:
            return _json_value(row[key])
    return None


def _is_cursor_timestamp_column(column: str) -> bool:
    name = column.lower()
    return name in _CURSOR_COLUMNS or ("cursor" in name and ("time" in name or name.endswith("_at")))


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
