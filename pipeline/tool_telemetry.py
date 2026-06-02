# Created: 2026-05-28
# Purpose: Tool call success/failure telemetry — data foundation for self-improvement
# Dependencies: SQLite (stdlib), pipeline.data_paths
# Test Status: under review

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_RECENT_FAILURES_LIMIT = 200


def _db_path() -> Path:
    # 영속 데이터 루트에 둔다 — repo_data_dir()(번들 내 data/)은 onefile 에서
    # 읽기전용/임시(_MEIPASS)라 SQLite write 가 깨진다.
    try:
        from pipeline.data_paths import data_dir
        return data_dir() / "tool_telemetry.db"
    except Exception:
        return Path(__file__).parent.parent / "data" / "tool_telemetry.db"


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        p = _db_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        _CONN = sqlite3.connect(str(p), check_same_thread=False, isolation_level=None)
        _CONN.execute("PRAGMA journal_mode=WAL")
        _CONN.executescript("""
            CREATE TABLE IF NOT EXISTS tool_stats (
                name TEXT PRIMARY KEY,
                calls INTEGER NOT NULL DEFAULT 0,
                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                total_ms INTEGER NOT NULL DEFAULT 0,
                last_called_ts TEXT,
                last_status TEXT
            );
            CREATE TABLE IF NOT EXISTS tool_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                name TEXT NOT NULL,
                error TEXT,
                args_summary TEXT,
                duration_ms INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_tool_failures_ts ON tool_failures(ts);
            CREATE INDEX IF NOT EXISTS idx_tool_failures_name ON tool_failures(name);
        """)
    return _CONN


def record_call(name: str, success: bool, duration_ms: int,
                error: str | None = None, args: dict | None = None) -> None:
    """Record a single tool call. On failure, also saves details to tool_failures."""
    if not name or name.startswith("__"):
        return
    with _LOCK:
        try:
            c = _conn()
            ts = datetime.now(KST).isoformat()
            status = "ok" if success else "error"
            c.execute("""
                INSERT INTO tool_stats(name, calls, successes, failures, total_ms, last_called_ts, last_status)
                VALUES(?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    calls = calls + 1,
                    successes = successes + excluded.successes,
                    failures = failures + excluded.failures,
                    total_ms = total_ms + excluded.total_ms,
                    last_called_ts = excluded.last_called_ts,
                    last_status = excluded.last_status
            """, (name, 1 if success else 0, 0 if success else 1, duration_ms, ts, status))

            if not success:
                args_str = ""
                if args:
                    try:
                        args_str = json.dumps(
                            {k: str(v)[:80] for k, v in args.items()},
                            ensure_ascii=False,
                        )[:300]
                    except Exception:
                        args_str = str(args)[:300]
                c.execute("""
                    INSERT INTO tool_failures(ts, name, error, args_summary, duration_ms)
                    VALUES(?, ?, ?, ?, ?)
                """, (ts, name, (error or "")[:500], args_str, duration_ms))
                # Trim old failure records
                c.execute("""
                    DELETE FROM tool_failures WHERE id NOT IN (
                        SELECT id FROM tool_failures ORDER BY id DESC LIMIT ?
                    )
                """, (_RECENT_FAILURES_LIMIT,))
        except Exception:
            pass  # telemetry failure must not block tool calls


def get_stats(limit: int = 50, order_by: str = "failures") -> list[dict]:
    """Per-tool statistics. order_by ∈ {failures, calls, error_rate, last}."""
    with _LOCK:
        c = _conn()
        order_sql = {
            "failures": "failures DESC, calls DESC",
            "calls": "calls DESC",
            "error_rate": "(CAST(failures AS REAL) / NULLIF(calls,0)) DESC, calls DESC",
            "last": "last_called_ts DESC",
        }.get(order_by, "failures DESC, calls DESC")
        rows = c.execute(f"""
            SELECT name, calls, successes, failures, total_ms, last_called_ts, last_status
            FROM tool_stats
            ORDER BY {order_sql}
            LIMIT ?
        """, (limit,)).fetchall()
    out = []
    for name, calls, succ, fail, total_ms, last_ts, last_status in rows:
        err_rate = (fail / calls) if calls else 0.0
        avg_ms = (total_ms / calls) if calls else 0
        out.append({
            "name": name,
            "calls": calls,
            "successes": succ,
            "failures": fail,
            "error_rate": round(err_rate, 3),
            "avg_ms": int(avg_ms),
            "last_called_ts": last_ts,
            "last_status": last_status,
        })
    return out


def get_recent_failures(limit: int = 20, since_iso: str | None = None,
                        name: str | None = None) -> list[dict]:
    """Recent failure log. Filter by time with since_iso, by tool name with name."""
    with _LOCK:
        c = _conn()
        sql = "SELECT ts, name, error, args_summary, duration_ms FROM tool_failures WHERE 1=1"
        params: list = []
        if since_iso:
            sql += " AND ts >= ?"
            params.append(since_iso)
        if name:
            sql += " AND name = ?"
            params.append(name)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = c.execute(sql, params).fetchall()
    return [
        {"ts": ts, "name": n, "error": err, "args_summary": args, "duration_ms": dur}
        for ts, n, err, args, dur in rows
    ]


def summary() -> dict:
    """Overall statistics summary — for dashboard use."""
    with _LOCK:
        c = _conn()
        row = c.execute("""
            SELECT
                COUNT(*) AS tool_count,
                SUM(calls) AS total_calls,
                SUM(successes) AS total_succ,
                SUM(failures) AS total_fail
            FROM tool_stats
        """).fetchone()
    tool_count, total_calls, total_succ, total_fail = row
    total_calls = total_calls or 0
    total_succ = total_succ or 0
    total_fail = total_fail or 0
    return {
        "tool_count": tool_count or 0,
        "total_calls": total_calls,
        "total_successes": total_succ,
        "total_failures": total_fail,
        "overall_error_rate": round((total_fail / total_calls) if total_calls else 0.0, 4),
    }
