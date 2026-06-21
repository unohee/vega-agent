# Created: 2026-06-21
# Purpose: Read-only iMessage chat.db importer for local SQLite fixtures/exports.
# Dependencies: sqlite3 (stdlib)

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
DEFAULT_SOURCE_DB = Path.home() / "Library" / "Messages" / "chat.db"


class IMessageImportPermissionError(PermissionError):
    """Raised when macOS privacy/TCC or filesystem permissions block chat.db access."""


@dataclass(frozen=True)
class ImportResult:
    imported: int
    skipped: int


def import_imessages(source_db: str | Path | None = None, dest_db: str | Path | None = None) -> ImportResult:
    """Import iMessage rows from source chat.db into a destination SQLite database.

    The source connection is opened read-only via SQLite URI and only the minimal
    Messages schema is queried: message, handle, chat, chat_message_join.
    """

    source_path = Path(source_db).expanduser() if source_db is not None else DEFAULT_SOURCE_DB
    if dest_db is None:
        raise ValueError("dest_db is required")
    dest_path = Path(dest_db).expanduser()
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        src = _connect_source(source_path)
    except (PermissionError, sqlite3.OperationalError) as exc:
        if isinstance(exc, PermissionError) or _looks_permission_denied(exc):
            raise IMessageImportPermissionError(f"permission denied opening iMessage database: {source_path}") from exc
        raise

    try:
        with src:
            rows = list(_iter_source_rows(src))
    finally:
        src.close()

    with sqlite3.connect(dest_path) as dst:
        _ensure_destination_schema(dst)
        before = dst.total_changes
        for row in rows:
            external_id = _external_id(row["guid"], row["message_id"])
            dst.execute(
                """
                INSERT OR IGNORE INTO imessage_messages
                    (external_id, sender, timestamp, chat_identifier, text, is_from_me, source_guid, source_rowid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    external_id,
                    _sender(row["is_from_me"], row["handle_id"]),
                    _convert_imessage_timestamp(row["date"]),
                    row["chat_identifier"],
                    row["text"],
                    int(row["is_from_me"] or 0),
                    row["guid"],
                    int(row["message_id"]),
                ),
            )
        imported = dst.total_changes - before
    return ImportResult(imported=imported, skipped=len(rows) - imported)


def _connect_source(source_path: Path) -> sqlite3.Connection:
    uri = source_path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def _ensure_destination_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS imessage_messages (
            external_id TEXT PRIMARY KEY,
            sender TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            chat_identifier TEXT,
            text TEXT,
            is_from_me INTEGER NOT NULL,
            source_guid TEXT,
            source_rowid INTEGER NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_messages_external_id ON imessage_messages(external_id)")


def _iter_source_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            message.ROWID AS message_id,
            message.guid AS guid,
            message.text AS text,
            message.date AS date,
            message.is_from_me AS is_from_me,
            handle.id AS handle_id,
            chat.chat_identifier AS chat_identifier
        FROM message
        LEFT JOIN handle ON handle.ROWID = message.handle_id
        LEFT JOIN chat_message_join ON chat_message_join.message_id = message.ROWID
        LEFT JOIN chat ON chat.ROWID = chat_message_join.chat_id
        WHERE message.text IS NOT NULL AND message.text != ''
        ORDER BY message.date, message.ROWID
        """
    ).fetchall()


def _sender(is_from_me: int | None, handle_id: str | None) -> str:
    if int(is_from_me or 0) == 1:
        return "me"
    return handle_id or "unknown"


def _convert_imessage_timestamp(value: int | float | None) -> str:
    if value is None:
        return APPLE_EPOCH.isoformat()
    raw = float(value)
    seconds = raw / 1_000_000_000 if abs(raw) > 10_000_000_000 else raw
    return (APPLE_EPOCH + timedelta(seconds=seconds)).isoformat()


def _external_id(guid: str | None, rowid: int) -> str:
    stable_source_id = guid or str(rowid)
    return "imessage:" + hashlib.sha256(stable_source_id.encode("utf-8")).hexdigest()


def _looks_permission_denied(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "permission" in msg and ("denied" in msg or "not authorized" in msg or "operation not permitted" in msg)


__all__ = [
    "APPLE_EPOCH",
    "DEFAULT_SOURCE_DB",
    "IMessageImportPermissionError",
    "ImportResult",
    "import_imessages",
]
