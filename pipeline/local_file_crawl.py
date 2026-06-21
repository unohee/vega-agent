# Created: 2026-06-20
# Purpose: Minimal local-file crawl connector with SQLite checkpoints.
# Dependencies: stdlib, pipeline/data_paths.py

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.data_paths import db_path

SOURCE = "local_file"
SENDER = "document"
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".markdown", ".rst", ".log"})
_UUID_NAMESPACE = uuid.UUID("7a84f5c6-4c4a-4a07-97d7-75f5f0fdad30")


def crawl_local_files(directories: Iterable[str | Path]) -> dict[str, Any]:
    """Scan directories and upsert eligible text files into canonical messages.

    Returns counters plus per-path error records. A second run over unchanged files
    is a no-op because local_file_checkpoints stores absolute path, mtime, size,
    and content hash in the same transaction as the message upsert.
    """
    result: dict[str, Any] = {
        "scanned": 0,
        "eligible": 0,
        "ingested": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }

    with _connect() as conn:
        _ensure_schema(conn)
        for directory in directories:
            root = Path(directory).expanduser()
            if not root.exists():
                _record_error(result, root, "not_found", "directory does not exist")
                continue
            if not root.is_dir():
                _record_error(result, root, "not_directory", "path is not a directory")
                continue

            for path in _iter_files(root, result):
                result["scanned"] += 1
                if path.suffix.lower() not in TEXT_EXTENSIONS:
                    result["skipped"] += 1
                    continue
                result["eligible"] += 1
                _process_file(conn, path, result)

    return result


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            uuid        TEXT PRIMARY KEY,
            source      TEXT NOT NULL DEFAULT 'vega',
            name        TEXT NOT NULL DEFAULT 'VEGA 세션',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            msg_count   INTEGER NOT NULL DEFAULT 0,
            working_dir TEXT,
            archived    INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            uuid        TEXT PRIMARY KEY,
            source      TEXT NOT NULL DEFAULT 'vega',
            conv_uuid   TEXT NOT NULL,
            sender      TEXT NOT NULL,
            text        TEXT NOT NULL,
            char_len    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            usage_meta  TEXT,
            events      TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_conv "
        "ON messages(source, conv_uuid, created_at)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS local_file_checkpoints (
            absolute_path TEXT PRIMARY KEY,
            mtime_ns      INTEGER NOT NULL,
            size          INTEGER NOT NULL,
            sha256        TEXT NOT NULL,
            conv_uuid     TEXT NOT NULL,
            message_uuid  TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
    """)

    conv_cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    if "working_dir" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN working_dir TEXT")
    if "archived" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")

    msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "usage_meta" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN usage_meta TEXT")
    if "events" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN events TEXT")


def _iter_files(root: Path, result: dict[str, Any]) -> Iterable[Path]:
    try:
        paths = sorted(root.rglob("*"))
    except OSError as exc:
        _record_error(result, root, "scan_error", str(exc))
        return []
    return (path for path in paths if path.is_file())


def _process_file(conn: sqlite3.Connection, path: Path, result: dict[str, Any]) -> None:
    try:
        stat = path.stat()
        data = path.read_bytes()
    except OSError as exc:
        result["skipped"] += 1
        _record_error(result, path, "read_error", str(exc))
        return

    if b"\x00" in data:
        result["skipped"] += 1
        _record_error(result, path, "binary_file", "NUL byte found in eligible extension")
        return

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        result["skipped"] += 1
        _record_error(result, path, "decode_error", str(exc))
        return

    absolute_path = str(path.resolve())
    digest = hashlib.sha256(data).hexdigest()
    checkpoint = conn.execute(
        """SELECT sha256, mtime_ns, message_uuid
           FROM local_file_checkpoints
           WHERE absolute_path=?""",
        (absolute_path,),
    ).fetchone()
    if checkpoint and checkpoint["sha256"] == digest and checkpoint["mtime_ns"] == stat.st_mtime_ns:
        result["skipped"] += 1
        return

    conv_uuid = _stable_uuid(f"conv:{absolute_path}")
    message_uuid = _stable_uuid(f"message:{absolute_path}")
    now = _now()
    usage_meta = json.dumps(
        {"path": absolute_path, "sha256": digest, "mtime_ns": stat.st_mtime_ns, "size": stat.st_size},
        ensure_ascii=False,
        sort_keys=True,
    )
    existed = checkpoint is not None or _message_exists(conn, message_uuid)

    with conn:
        conn.execute(
            """INSERT INTO conversations (uuid, source, name, created_at, updated_at, msg_count)
               VALUES (?, ?, ?, ?, ?, 1)
               ON CONFLICT(uuid) DO UPDATE SET
                   source=excluded.source,
                   name=excluded.name,
                   updated_at=excluded.updated_at,
                   msg_count=CASE WHEN conversations.msg_count < 1 THEN 1 ELSE conversations.msg_count END""",
            (conv_uuid, SOURCE, f"Local file: {path.name}", now, now),
        )
        conn.execute(
            """INSERT INTO messages
                   (uuid, source, conv_uuid, sender, text, char_len, created_at, updated_at, usage_meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(uuid) DO UPDATE SET
                   source=excluded.source,
                   conv_uuid=excluded.conv_uuid,
                   sender=excluded.sender,
                   text=excluded.text,
                   char_len=excluded.char_len,
                   updated_at=excluded.updated_at,
                   usage_meta=excluded.usage_meta""",
            (message_uuid, SOURCE, conv_uuid, SENDER, text, len(text), now, now, usage_meta),
        )
        conn.execute(
            """INSERT INTO local_file_checkpoints
                   (absolute_path, mtime_ns, size, sha256, conv_uuid, message_uuid, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(absolute_path) DO UPDATE SET
                   mtime_ns=excluded.mtime_ns,
                   size=excluded.size,
                   sha256=excluded.sha256,
                   conv_uuid=excluded.conv_uuid,
                   message_uuid=excluded.message_uuid,
                   updated_at=excluded.updated_at""",
            (absolute_path, stat.st_mtime_ns, stat.st_size, digest, conv_uuid, message_uuid, now),
        )

    result["updated" if existed else "ingested"] += 1


def _message_exists(conn: sqlite3.Connection, message_uuid: str) -> bool:
    row = conn.execute("SELECT 1 FROM messages WHERE uuid=?", (message_uuid,)).fetchone()
    return row is not None


def _stable_uuid(value: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, value))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_error(result: dict[str, Any], path: Path, code: str, message: str) -> None:
    result["errors"].append({"path": str(path), "code": code, "message": message})
