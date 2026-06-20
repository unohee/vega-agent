# Created: 2026-06-20
# Purpose: Read-only iMessage chat.db importer into the VEGA SQLite database.
# Dependencies: stdlib, pipeline.data_paths

from __future__ import annotations

import errno
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pipeline.data_paths import db_path

APPLE_EPOCH_UNIX_SECONDS = 978_307_200
DEFAULT_SOURCE_PATH = Path.home() / "Library" / "Messages" / "chat.db"
SELF_SENDER = "__self__"
UNKNOWN_SENDER = "__unknown__"

_PERMISSION_GUIDANCE = (
    "Cannot read iMessage chat.db. Grant Full Disk Access to the app or shell "
    "running VEGA: System Settings -> Privacy & Security -> Full Disk Access, "
    "enable Terminal/iTerm/VEGA/Python as applicable, then restart it. Also "
    "confirm ~/Library/Messages/chat.db exists and is readable."
)

_PERMISSION_SQLITE_FRAGMENTS = (
    "unable to open database file",
    "authorization denied",
    "not authorized",
    "operation not permitted",
    "permission denied",
    "access denied",
)


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS imessage_messages (
    external_id     TEXT NOT NULL UNIQUE,
    sender          TEXT,
    timestamp       TEXT,
    chat_identifier TEXT,
    text            TEXT,
    imported_at     TEXT NOT NULL
)
"""

SOURCE_QUERY_SQL = """
SELECT
    m.ROWID AS message_rowid,
    m.guid AS guid,
    m.date AS message_date,
    m.text AS text,
    m.is_from_me AS is_from_me,
    h.id AS handle_id,
    c.ROWID AS chat_rowid,
    c.chat_identifier AS chat_identifier
FROM message AS m
LEFT JOIN handle AS h ON h.ROWID = m.handle_id
LEFT JOIN chat_message_join AS cmj ON cmj.message_id = m.ROWID
LEFT JOIN chat AS c ON c.ROWID = cmj.chat_id
ORDER BY m.ROWID, c.ROWID
"""

INSERT_MESSAGE_SQL = """
INSERT OR IGNORE INTO imessage_messages (
    external_id,
    sender,
    timestamp,
    chat_identifier,
    text,
    imported_at
) VALUES (?, ?, ?, ?, ?, ?)
"""


def import_imessages(source_path: Path | str | None = None) -> dict[str, Any]:
    """Import iMessage rows into ``db_path()`` using a read-only source connection.

    Timestamps are stored as UTC ISO-8601 strings with a ``Z`` suffix. Apple
    timestamp units are inferred defensively: nanoseconds, microseconds,
    milliseconds, or seconds since 2001-01-01 00:00:00 UTC.
    """
    source = _normalize_source_path(source_path)
    destination = db_path()

    source_conn: sqlite3.Connection | None = None
    dest_conn: sqlite3.Connection | None = None
    try:
        source_conn = sqlite3.connect(_sqlite_readonly_uri(source), uri=True, timeout=5)
        source_conn.row_factory = sqlite3.Row

        destination.parent.mkdir(parents=True, exist_ok=True)
        dest_conn = sqlite3.connect(str(destination), timeout=10)
        dest_conn.execute(CREATE_TABLE_SQL)

        imported_at = _utc_now_iso()
        imported_count = 0
        skipped_count = 0

        for row in source_conn.execute(SOURCE_QUERY_SQL):
            cursor = dest_conn.execute(
                INSERT_MESSAGE_SQL,
                (
                    _external_id(row),
                    _sender(row),
                    _apple_timestamp_to_utc_iso(row["message_date"]),
                    _chat_identifier(row),
                    row["text"],
                    imported_at,
                ),
            )
            if cursor.rowcount == 1:
                imported_count += 1
            else:
                skipped_count += 1

        dest_conn.commit()
        return {
            "status": "ok",
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "source_path": str(source),
            "destination_path": str(destination),
        }
    except (sqlite3.OperationalError, PermissionError, OSError) as exc:
        if _is_permission_or_read_access_error(exc):
            if dest_conn is not None:
                dest_conn.rollback()
            return {
                "status": "permission_error",
                "imported_count": 0,
                "skipped_count": 0,
                "source_path": str(source),
                "destination_path": str(destination),
                "message": _PERMISSION_GUIDANCE,
                "error": str(exc),
            }
        raise
    finally:
        if source_conn is not None:
            source_conn.close()
        if dest_conn is not None:
            dest_conn.close()


def _normalize_source_path(source_path: Path | str | None) -> Path:
    source = Path(source_path).expanduser() if source_path is not None else DEFAULT_SOURCE_PATH
    return source if source.is_absolute() else source.resolve(strict=False)


def _sqlite_readonly_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/:')}?mode=ro"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _apple_timestamp_to_utc_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(raw):
        return None

    absolute = abs(raw)
    if absolute >= 100_000_000_000_000_000:
        apple_seconds = raw / 1_000_000_000
    elif absolute >= 100_000_000_000_000:
        apple_seconds = raw / 1_000_000
    elif absolute >= 100_000_000_000:
        apple_seconds = raw / 1_000
    else:
        apple_seconds = raw

    try:
        timestamp = datetime.fromtimestamp(
            APPLE_EPOCH_UNIX_SECONDS + apple_seconds,
            tz=timezone.utc,
        )
    except (OverflowError, OSError, ValueError):
        return None
    return timestamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sender(row: sqlite3.Row) -> str:
    handle_id = row["handle_id"]
    if handle_id:
        return str(handle_id)
    if row["is_from_me"] == 1:
        return SELF_SENDER
    return UNKNOWN_SENDER


def _chat_identifier(row: sqlite3.Row) -> str | None:
    chat_identifier = row["chat_identifier"]
    if chat_identifier:
        return str(chat_identifier)
    chat_rowid = row["chat_rowid"]
    return f"chat_rowid:{chat_rowid}" if chat_rowid is not None else None


def _external_id(row: sqlite3.Row) -> str:
    guid = row["guid"]
    if guid and str(guid).strip():
        return str(guid).strip()

    message_rowid = row["message_rowid"]
    chat_identifier = _chat_identifier(row) or "chat:unknown"
    return f"message_rowid:{message_rowid}|{chat_identifier}"


def _is_permission_or_read_access_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in {errno.EACCES, errno.EPERM}:
        return True
    if isinstance(exc, sqlite3.OperationalError):
        message = str(exc).lower()
        return any(fragment in message for fragment in _PERMISSION_SQLITE_FRAGMENTS)
    return False


__all__ = ["import_imessages"]
