# Created: 2026-06-20
# Purpose: Read-only iMessage chat.db importer into canonical VEGA DB.
# Dependencies: sqlite3, pipeline.data_paths

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from pipeline.data_paths import db_path

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
DEFAULT_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
DEFAULT_SOURCE_DB = DEFAULT_CHAT_DB
FULL_DISK_ACCESS_HELP = (
    "Unable to read iMessage chat.db. Grant Full Disk Access to Terminal, Python, "
    "or the VEGA app in System Settings > Privacy & Security > Full Disk Access, "
    "then retry."
)


class IMessageImportError(RuntimeError):
    """Deterministic user-actionable failure for missing/TCC-blocked chat.db."""


class IMessageImportPermissionError(IMessageImportError, PermissionError):
    """Raised when macOS privacy/TCC or filesystem permissions block chat.db access."""


@dataclass(frozen=True)
class ImportResult:
    source_db: str
    destination_db: str
    scanned: int
    inserted: int

    @property
    def imported(self) -> int:
        return self.inserted

    @property
    def skipped(self) -> int:
        return self.scanned - self.inserted


@dataclass(frozen=True)
class IMessageRow:
    external_id: str
    sender: str
    timestamp: str
    chat_identifier: str
    text: str
    is_from_me: int
    source_guid: str | None
    source_rowid: int


def _readonly_uri(path: Path) -> str:
    return f"file:{quote(str(path.expanduser()), safe='/')}?mode=ro"


def _connect_source(path: Path) -> sqlite3.Connection:
    source = path.expanduser()
    if not source.exists():
        raise IMessageImportError(f"{FULL_DISK_ACCESS_HELP} Missing source database: {source}")
    try:
        conn = sqlite3.connect(_readonly_uri(source), uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except PermissionError as exc:
        raise IMessageImportPermissionError(f"{FULL_DISK_ACCESS_HELP} Source database: {source}") from exc
    except sqlite3.OperationalError as exc:
        if _looks_permission_denied(exc):
            raise IMessageImportPermissionError(
                f"permission denied opening iMessage database: {source}. {FULL_DISK_ACCESS_HELP}"
            ) from exc
        raise IMessageImportError(f"{FULL_DISK_ACCESS_HELP} Source database: {source}; sqlite error: {exc}") from exc


def _apple_timestamp(value: int | float | None) -> str:
    if value is None:
        return ""
    seconds = float(value)
    if abs(seconds) > 1_000_000_000_000:
        seconds /= 1_000_000_000
    return (APPLE_EPOCH + timedelta(seconds=seconds)).isoformat()


_convert_imessage_timestamp = _apple_timestamp


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
        raise IMessageImportPermissionError(FULL_DISK_ACCESS_HELP) from exc
    except sqlite3.OperationalError as exc:
        if _looks_permission_denied(exc):
            raise IMessageImportPermissionError(f"{FULL_DISK_ACCESS_HELP} sqlite error: {exc}") from exc
        raise IMessageImportError(f"{FULL_DISK_ACCESS_HELP} sqlite error: {exc}") from exc

    result: list[IMessageRow] = []
    for row in rows:
        message_rowid = int(row["message_rowid"])
        message_guid = row["message_guid"]
        chat_rowid = row["chat_rowid"]
        chat_key = "" if chat_rowid is None else str(chat_rowid)
        external_id = f"imessage:{message_rowid}:{chat_key}:{message_guid}"
        is_from_me = int(row["is_from_me"] or 0)
        result.append(
            IMessageRow(
                external_id=external_id,
                sender=_sender(is_from_me, row["handle_identifier"]),
                timestamp=_apple_timestamp(row["message_date"]),
                chat_identifier=row["chat_identifier"] or "",
                text=row["text"],
                is_from_me=is_from_me,
                source_guid=message_guid,
                source_rowid=message_rowid,
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
            is_from_me INTEGER NOT NULL DEFAULT 0,
            source_guid TEXT,
            source_rowid INTEGER,
            imported_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_imessage_messages_external_id "
        "ON imessage_messages(external_id)"
    )


_ensure_destination_schema = _ensure_destination


def _insert_messages(conn: sqlite3.Connection, messages: Iterable[IMessageRow]) -> int:
    before = conn.total_changes
    conn.executemany(
        """
        INSERT OR IGNORE INTO imessage_messages
            (external_id, sender, timestamp, chat_identifier, text, is_from_me, source_guid, source_rowid)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                message.external_id,
                message.sender,
                message.timestamp,
                message.chat_identifier,
                message.text,
                message.is_from_me,
                message.source_guid,
                message.source_rowid,
            )
            for message in messages
        ],
    )
    return conn.total_changes - before


def _sender(is_from_me: int | None, handle_id: str | None) -> str:
    if int(is_from_me or 0) == 1:
        return "me"
    return handle_id or "unknown"


def _looks_permission_denied(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "permission" in msg and ("denied" in msg or "not authorized" in msg or "operation not permitted" in msg)


def import_imessages(
    source_db: str | Path | None = None,
    destination_db: str | Path | None = None,
    dest_db: str | Path | None = None,
) -> ImportResult:
    """Import readable iMessage text rows into canonical VEGA DB, deduped by external_id."""
    if destination_db is not None and dest_db is not None and Path(destination_db) != Path(dest_db):
        raise ValueError("destination_db and dest_db refer to different paths")

    source = Path(source_db).expanduser() if source_db is not None else DEFAULT_CHAT_DB
    destination_arg = destination_db if destination_db is not None else dest_db
    destination = Path(destination_arg).expanduser() if destination_arg is not None else db_path()
    destination.parent.mkdir(parents=True, exist_ok=True)

    from contextlib import closing  # INT-2236: sqlite Connection __exit__ 는 commit 만 — close 안 함
    try:
        with closing(_connect_source(source)) as src:
            messages = _fetch_messages(src)
    except (PermissionError, sqlite3.OperationalError) as exc:
        if isinstance(exc, IMessageImportError):
            raise
        if isinstance(exc, PermissionError) or _looks_permission_denied(exc):
            raise IMessageImportPermissionError(
                f"permission denied opening iMessage database: {source}. {FULL_DISK_ACCESS_HELP}"
            ) from exc
        raise

    # closing(close 보장) + with dst(transaction commit) 둘 다 필요 (INT-2236).
    with closing(sqlite3.connect(str(destination))) as dst, dst:
        _ensure_destination(dst)
        inserted = _insert_messages(dst, messages)

    return ImportResult(
        source_db=str(source),
        destination_db=str(destination),
        scanned=len(messages),
        inserted=inserted,
    )


__all__ = [
    "APPLE_EPOCH",
    "DEFAULT_CHAT_DB",
    "DEFAULT_SOURCE_DB",
    "FULL_DISK_ACCESS_HELP",
    "IMessageImportError",
    "IMessageImportPermissionError",
    "ImportResult",
    "import_imessages",
]
