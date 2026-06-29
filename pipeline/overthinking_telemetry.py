# Created: 2026-06-24
# Purpose: INT-1893 turn-level overthinking metrics persistence (L4 telemetry).
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def _db_path() -> Path:
    try:
        from pipeline.data_paths import data_dir
        return data_dir() / "overthinking_telemetry.db"
    except Exception:
        return Path(__file__).resolve().parent.parent / "data" / "overthinking_telemetry.db"


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path))
    c.execute("""
        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            session_id TEXT,
            load TEXT,
            max_rounds INTEGER,
            max_tool_rounds INTEGER,
            actual_rounds INTEGER,
            tool_rounds INTEGER,
            output_tokens INTEGER,
            elapsed_sec REAL,
            early_stop_search INTEGER,
            tool_round_cap_hit INTEGER
        )
    """)
    c.commit()
    return c


def record_turn(session_id: str, stats: dict) -> None:
    if not stats:
        return
    try:
        c = _conn()
        c.execute(
            """INSERT INTO turns (
                ts, session_id, load, max_rounds, max_tool_rounds,
                actual_rounds, tool_rounds, output_tokens, elapsed_sec,
                early_stop_search, tool_round_cap_hit
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                time.time(),
                session_id or "",
                stats.get("load"),
                stats.get("max_rounds"),
                stats.get("max_tool_rounds"),
                stats.get("actual_rounds"),
                stats.get("tool_rounds"),
                stats.get("output_tokens"),
                stats.get("elapsed_sec"),
                1 if stats.get("early_stop_search") else 0,
                1 if stats.get("tool_round_cap_hit") else 0,
            ),
        )
        c.commit()
        c.close()
    except Exception:
        pass


def recent_light_stats(days: float = 7.0) -> dict:
    """Aggregate light-load turns for report_overthinking.py."""
    since = time.time() - days * 86400
    try:
        c = _conn()
        rows = c.execute(
            """SELECT actual_rounds, tool_rounds, output_tokens, elapsed_sec
               FROM turns WHERE load='light' AND ts >= ? ORDER BY ts DESC""",
            (since,),
        ).fetchall()
        c.close()
    except Exception:
        return {"n": 0}
    if not rows:
        return {"n": 0}

    def _pct(vals: list[float], p: float) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        i = min(len(s) - 1, int(len(s) * p))
        return round(s[i], 2)

    ar = [r[0] or 0 for r in rows]
    tr = [r[1] or 0 for r in rows]
    ot = [r[2] or 0 for r in rows]
    el = [r[3] or 0 for r in rows]
    return {
        "n": len(rows),
        "median_actual_rounds": _pct(ar, 0.5),
        "p95_actual_rounds": _pct(ar, 0.95),
        "median_tool_rounds": _pct(tr, 0.5),
        "p95_tool_rounds": _pct(tr, 0.95),
        "median_output_tokens": _pct(ot, 0.5),
        "p95_elapsed_sec": _pct(el, 0.95),
    }
