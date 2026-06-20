# Created: 2026-06-10
# Purpose: Google freshness cursors/checkpoints stored in canonical VEGA DB.
# Dependencies: sqlite3, pipeline.data_paths.db_path

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pipeline.data_paths import db_path


def _normalize_source(source: str) -> str:
    src = str(source).strip()
    if not src:
        raise ValueError("source must be a non-empty string")
    return src


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_google_freshness_tables() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS google_sync_cursors (
                source TEXT PRIMARY KEY,
                cursor_value TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS extraction_checkpoints (
                source TEXT PRIMARY KEY,
                checkpoint_value TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )


def get_google_sync_cursor(source: str) -> str | None:
    src = _normalize_source(source)
    init_google_freshness_tables()
    with _connect() as conn:
        row = conn.execute(
            "SELECT cursor_value FROM google_sync_cursors WHERE source = ?",
            (src,),
        ).fetchone()
        return None if row is None else row[0]


def update_google_sync_cursor(source: str, cursor_value: str) -> None:
    src = _normalize_source(source)
    init_google_freshness_tables()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO google_sync_cursors (source, cursor_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                cursor_value = excluded.cursor_value,
                updated_at = excluded.updated_at
            """,
            (src, cursor_value, _utc_now_iso()),
        )


def get_extraction_checkpoint(source: str) -> str | None:
    src = _normalize_source(source)
    init_google_freshness_tables()
    with _connect() as conn:
        row = conn.execute(
            "SELECT checkpoint_value FROM extraction_checkpoints WHERE source = ?",
            (src,),
        ).fetchone()
        return None if row is None else row[0]


def update_extraction_checkpoint(source: str, checkpoint_value: str) -> None:
    src = _normalize_source(source)
    init_google_freshness_tables()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO extraction_checkpoints (source, checkpoint_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                checkpoint_value = excluded.checkpoint_value,
                updated_at = excluded.updated_at
            """,
            (src, checkpoint_value, _utc_now_iso()),
        )
