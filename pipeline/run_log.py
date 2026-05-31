# Created: 2026-05-27
# Purpose: Persist tool call execution logs — per-session run log (RES-224)
# Dependencies: pipeline/data_paths.py, sqlite3
# Test Status: in review

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


def _db() -> Path:
    try:
        from pipeline.data_paths import db_path
        return db_path()
    except Exception:
        return Path.home() / "Library/Application Support/VEGA/agent.db"


def _ensure_table() -> None:
    with sqlite3.connect(str(_db())) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS run_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_uuid TEXT NOT NULL,
                call_id     TEXT,
                tool_name   TEXT NOT NULL,
                args_json   TEXT,
                result_json TEXT,
                started_at  REAL NOT NULL,   -- monotonic start
                elapsed_ms  INTEGER,         -- None until done
                status      TEXT NOT NULL DEFAULT 'started',  -- started | ok | error
                error       TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS run_log_session ON run_log(session_uuid)")


_table_ensured = False


def _init():
    global _table_ensured
    if not _table_ensured:
        _ensure_table()
        _table_ensured = True


def record_start(
    session_uuid: str,
    tool_name: str,
    args: dict,
    call_id: str = "",
) -> int:
    """Record a tool_start event. Returns the new row id."""
    _init()
    with sqlite3.connect(str(_db())) as conn:
        cur = conn.execute(
            "INSERT INTO run_log (session_uuid, call_id, tool_name, args_json, started_at, status) VALUES (?,?,?,?,?,?)",
            (session_uuid, call_id, tool_name,
             json.dumps(args, ensure_ascii=False)[:4096],
             time.monotonic(), "started"),
        )
        return cur.lastrowid


def record_done(
    row_id: int,
    result: str,
    *,
    started_at: float | None = None,
) -> None:
    """Update the row with a tool_done event."""
    _init()
    elapsed = None
    if started_at is not None:
        elapsed = int((time.monotonic() - started_at) * 1000)
    with sqlite3.connect(str(_db())) as conn:
        conn.execute(
            "UPDATE run_log SET result_json=?, elapsed_ms=?, status='ok' WHERE id=?",
            (result[:4096] if result else None, elapsed, row_id),
        )


def record_error(row_id: int, error: str, *, started_at: float | None = None) -> None:
    """Record a tool failure."""
    _init()
    elapsed = None
    if started_at is not None:
        elapsed = int((time.monotonic() - started_at) * 1000)
    with sqlite3.connect(str(_db())) as conn:
        conn.execute(
            "UPDATE run_log SET error=?, elapsed_ms=?, status='error' WHERE id=?",
            (error[:1024], elapsed, row_id),
        )


def get_session_log(session_uuid: str, limit: int = 50) -> list[dict]:
    """Retrieve the run log for a specific session."""
    _init()
    with sqlite3.connect(str(_db())) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM run_log WHERE session_uuid=? ORDER BY id DESC LIMIT ?",
            (session_uuid, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_log(limit: int = 100) -> list[dict]:
    """Retrieve the most recent run log entries across all sessions."""
    _init()
    with sqlite3.connect(str(_db())) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM run_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
