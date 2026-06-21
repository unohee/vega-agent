# Created: 2026-06-20
# Purpose: Read-only Chrome/Chromium browser history ingestion into the VEGA SQLite DB.
# Dependencies: pipeline/data_paths.py, sqlite3 (stdlib)

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pipeline.data_paths import db_path

_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
_CONNECTOR_ENV = "VEGA_BROWSER_HISTORY_DB"
_CHECKPOINT_TABLE = "browser_history_checkpoints"
_VISITS_TABLE = "browser_history_visits"


class BrowserHistoryConfigError(ValueError):
    """Raised when the browser history source profile is not configured."""


def chrome_visit_time_to_iso(visit_time: int) -> str:
    """Convert Chrome/WebKit microseconds since 1601-01-01 UTC to ISO-8601 UTC."""
    return (_CHROME_EPOCH + timedelta(microseconds=int(visit_time))).isoformat()


def _resolve_profile_db_path(profile_db_path: Path | str | None) -> Path:
    raw_path = profile_db_path if profile_db_path is not None else os.environ.get(_CONNECTOR_ENV, "").strip()
    if not raw_path:
        raise BrowserHistoryConfigError(f"Set {_CONNECTOR_ENV} or pass profile_db_path")

    path = Path(raw_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Browser history DB not found: {path}")
    if not path.is_file():
        raise BrowserHistoryConfigError(f"Browser history path is not a file: {path}")
    return path


def _readonly_sqlite_uri(path: Path) -> str:
    return f"file:{quote(str(path))}?mode=ro&immutable=1"


def _copy_history_db(source_path: Path) -> Path:
    tmp = tempfile.NamedTemporaryFile(prefix="vega_browser_history_", suffix=".sqlite", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        shutil.copy2(source_path, tmp_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def _connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_readonly_sqlite_uri(path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_destination_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_VISITS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            visit_timestamp TEXT NOT NULL,
            source_profile TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            chrome_visit_id INTEGER NOT NULL,
            chrome_visit_time INTEGER NOT NULL,
            UNIQUE(source_profile, chrome_visit_id)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_CHECKPOINT_TABLE} (
            source_profile TEXT PRIMARY KEY,
            last_visit_time INTEGER NOT NULL DEFAULT 0,
            last_visit_id INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_VISITS_TABLE}_timestamp "
        f"ON {_VISITS_TABLE}(visit_timestamp)"
    )


def _get_checkpoint(conn: sqlite3.Connection, source_profile: str) -> tuple[int, int]:
    row = conn.execute(
        f"SELECT last_visit_time, last_visit_id FROM {_CHECKPOINT_TABLE} WHERE source_profile = ?",
        (source_profile,),
    ).fetchone()
    if row is None:
        return 0, 0
    return int(row["last_visit_time"]), int(row["last_visit_id"])


def _fetch_new_visits(
    source_conn: sqlite3.Connection,
    last_visit_time: int,
    last_visit_id: int,
    limit: int | None,
) -> list[sqlite3.Row]:
    query = """
        SELECT
            v.id AS visit_id,
            v.visit_time AS visit_time,
            u.url AS url,
            u.title AS title
        FROM visits v
        JOIN urls u ON u.id = v.url
        WHERE v.visit_time > ? OR (v.visit_time = ? AND v.id > ?)
        ORDER BY v.visit_time ASC, v.id ASC
    """
    params: list[Any] = [last_visit_time, last_visit_time, last_visit_id]
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be a positive integer")
        query += " LIMIT ?"
        params.append(limit)
    return source_conn.execute(query, params).fetchall()


def _open_destination() -> sqlite3.Connection:
    destination = db_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(destination))
    conn.row_factory = sqlite3.Row
    return conn


def import_recent_visits(profile_db_path: Path | str | None = None, limit: int | None = None) -> dict[str, Any]:
    """Import Chrome/Chromium visits newer than the stored checkpoint.

    The configured History SQLite file is copied to a temporary file before opening, so this
    connector never reads the live browser profile DB directly.
    """
    source_path = _resolve_profile_db_path(profile_db_path)
    source_profile = str(source_path)

    with _open_destination() as dest_conn:
        _ensure_destination_schema(dest_conn)
        last_visit_time, last_visit_id = _get_checkpoint(dest_conn, source_profile)

    temp_copy = _copy_history_db(source_path)
    try:
        with _connect_readonly(temp_copy) as source_conn:
            visits = _fetch_new_visits(source_conn, last_visit_time, last_visit_id, limit)
    finally:
        temp_copy.unlink(missing_ok=True)

    inserted = 0
    imported_at = datetime.now(UTC).isoformat()
    checkpoint_advanced = False

    with _open_destination() as dest_conn:
        _ensure_destination_schema(dest_conn)
        with dest_conn:
            for visit in visits:
                cursor = dest_conn.execute(
                    f"""
                    INSERT OR IGNORE INTO {_VISITS_TABLE} (
                        url,
                        title,
                        visit_timestamp,
                        source_profile,
                        imported_at,
                        chrome_visit_id,
                        chrome_visit_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        visit["url"],
                        visit["title"],
                        chrome_visit_time_to_iso(int(visit["visit_time"])),
                        source_profile,
                        imported_at,
                        int(visit["visit_id"]),
                        int(visit["visit_time"]),
                    ),
                )
                inserted += cursor.rowcount

            if visits:
                last_visit = visits[-1]
                updated_at = datetime.now(UTC).isoformat()
                dest_conn.execute(
                    f"""
                    INSERT INTO {_CHECKPOINT_TABLE} (
                        source_profile,
                        last_visit_time,
                        last_visit_id,
                        updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_profile) DO UPDATE SET
                        last_visit_time = excluded.last_visit_time,
                        last_visit_id = excluded.last_visit_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        source_profile,
                        int(last_visit["visit_time"]),
                        int(last_visit["visit_id"]),
                        updated_at,
                    ),
                )
                checkpoint_advanced = True

    return {
        "source_profile": source_profile,
        "source_rows": len(visits),
        "inserted": inserted,
        "ignored": len(visits) - inserted,
        "checkpoint_advanced": checkpoint_advanced,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Chrome/Chromium browser history into VEGA DB")
    parser.add_argument("profile_db_path", nargs="?", type=Path, help=f"History DB path; defaults to {_CONNECTOR_ENV}")
    parser.add_argument("--limit", type=int, default=None, help="Maximum visits to import")
    args = parser.parse_args()
    result = import_recent_visits(args.profile_db_path, args.limit)
    print(result)


if __name__ == "__main__":
    main()
