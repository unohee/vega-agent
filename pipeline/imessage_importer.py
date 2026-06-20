# Created: 2026-06-20
# Purpose: Read-only iMessage chat.db importer into canonical VEGA DB.
# Dependencies: sqlite3, pipeline.data_paths

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from pipeline.data_paths import db_path

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
FULL_DISK_ACCESS_HELP = (
    "Unable to read iMessage chat.db. Grant Full Disk Access to Terminal, Python, "
    "or the VEGA app in System Settings > Privacy & Security > Full Disk Access, "
    "then retry."
)


class IMessageImportError(RuntimeError):
    """Deterministic user-actionable failure for missing/TCC-blocked chat.db."""


@dataclass(frozen=True)
class ImportResult:
    source_db: str
    destination_db: str
    scanned: int
    inserted: int


@dataclass(frozen=True)
class IMessageRow:
    external_id: str
    sender: str
    timestamp: str
    chat_identifier: str
    text: str


def _readonly_uri(path: Path) -> str:
    return f"file:{quote(str(path.expanduser()), safe='/')}?mode=ro"


def _connect_source(path: Path) -> sqlite3.Connection:
    source = path.expanduser()
    if not source.exists():
        raise IMessageImportError(f"{FULL_DISK_ACCESS_HELP} Missing source database: {source}")
    try:
        return sqlite3.connect(_readonly_uri(source), uri=True)
    except PermissionError as exc:
        raise IMessageImportError(f"{FULL_DISK_ACCESS_HELP} Source database: {source}") from exc
    except sqlite3.OperationalError as exc:
        raise IMessageImportError(f"{FULL_DISK_ACCESS_HELP} Source database: {source}; sqlite error: {exc}") from exc


def _apple_timestamp(value: int | float | None) -> str:
    if value is None:
        return ""
    seconds = float(value)
    if abs(seconds) > 1_000_000_000_000:
        seconds /= 1_000_000_000
    return (APPLE_EPOCH + timedelta(seconds=seconds)).isoformat()


def _fetch_messages(conn: sqlite3.Connection) -> list[IMessageRow]:
    try:
        rows = conn.execute(
            """
            SELECT
                m.ROWID AS message_rowid,
                COALESCE(NULLIF(m.guid, ''), CAST(m.ROWID AS TEXT)) AS message_guid,
                m.date AS message_date,
                m.text AS text,
                m.is_from_me AS is_from_me,
                h.id AS handle_identifier,
                c.ROWID AS chat_rowid,
                c.chat_identifier AS chat_identifier
            FROM message AS m
            LEFT JOIN handle AS h ON h.ROWID = m.handle_id
            LEFT JOIN chat_message_join AS cmj ON cmj.message_id = m.ROWID
            LEFT JOIN chat AS c ON c.ROWID = cmj.chat_id
            WHERE m.text IS NOT NULL AND m.text != ''
            ORDER BY m.date, m.ROWID, c.ROWID
            """
        ).fetchall()
    except PermissionError as exc:
        raise IMessageImportError(FULL_DISK_ACCESS_HELP) from exc
    except sqlite3.OperationalError as exc:
        raise IMessageImportError(f"{FULL_DISK_ACCESS_HELP} sqlite error: {exc}") from exc

    result: list[IMessageRow] = []
    for row in rows:
        message_rowid, message_guid, message_date, text, is_from_me, handle_id, chat_rowid, chat_id = row
        chat_key = "" if chat_rowid is None else str(chat_rowid)
        external_id = f"imessage:{message_rowid}:{chat_key}:{message_guid}"
        sender = "me" if is_from_me else (handle_id or "unknown")
        result.append(
            IMessageRow(
                external_id=external_id,
                sender=sender,
                timestamp=_apple_timestamp(message_date),
                chat_identifier=chat_id or "",
                text=text,
            )
        )
    return result


def _ensure_destination(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS imessage_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL DEFAULT 'imessage',
            sender TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            chat_identifier TEXT NOT NULL,
            text TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_imessage_messages_timestamp "
        "ON imessage_messages(timestamp)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_imessage_messages_chat "
        "ON imessage_messages(chat_identifier)"
    )


def _insert_messages(conn: sqlite3.Connection, rows: Iterable[IMessageRow]) -> int:
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO imessage_messages
            (external_id, source, sender, timestamp, chat_identifier, text)
        VALUES (?, 'imessage', ?, ?, ?, ?)
        """,
        ((r.external_id, r.sender, r.timestamp, r.chat_identifier, r.text) for r in rows),
    )
    return conn.total_changes - before


def import_imessages(source_db: str | Path | None = None, destination_db: str | Path | None = None) -> ImportResult:
    """Import readable iMessage text rows into canonical VEGA DB, deduped by external_id."""
    source = Path(source_db).expanduser() if source_db is not None else DEFAULT_CHAT_DB
    destination = Path(destination_db).expanduser() if destination_db is not None else db_path()
    destination.parent.mkdir(parents=True, exist_ok=True)

    with _connect_source(source) as src:
        messages = _fetch_messages(src)

    with sqlite3.connect(str(destination)) as dst:
        _ensure_destination(dst)
        inserted = _insert_messages(dst, messages)

    return ImportResult(
        source_db=str(source),
        destination_db=str(destination),
        scanned=len(messages),
        inserted=inserted,
    )
