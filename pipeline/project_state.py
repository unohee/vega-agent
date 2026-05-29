# Created: 2026-05-19
# Purpose: Per-project state registry — metrics require a measurement date (as_of)
# Dependencies: sqlite3 (stdlib)
# Test Status: in review

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
from pipeline.data_paths import db_path as _db_path
DB_PATH = _db_path()

SEED_PROJECTS = ["ArtifactNet", "KYTE", "SoundAllAccess", "STONKS", "VEGA"]

# Fields that are JSON-serialized — Python objects stored as TEXT in SQLite
_JSON_FIELDS = {"metrics": "metrics_json", "risks": "risks_json",
                "next_actions": "next_actions_json"}
_PLAIN_FIELDS = {"status", "positioning"}


def _ensure_project_state_table():
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_state (
                name              TEXT PRIMARY KEY,
                status            TEXT,
                positioning       TEXT,
                metrics_json      TEXT,
                risks_json        TEXT,
                next_actions_json TEXT,
                updated_at        TEXT NOT NULL
            )
        """)
        conn.commit()


def seed_project_states():
    """Create empty active records for projects that don't exist yet. Idempotent."""
    _ensure_project_state_table()
    now = datetime.now(KST).isoformat()
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        for name in SEED_PROJECTS:
            conn.execute(
                "INSERT OR IGNORE INTO project_state "
                "(name, status, positioning, metrics_json, risks_json, next_actions_json, updated_at) "
                "VALUES (?, 'active', '', '{}', '[]', '[]', ?)",
                (name, now),
            )
        conn.commit()


def get_project_state(name: str) -> dict | None:
    """Retrieve project state. JSON fields are parsed before returning."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM project_state WHERE name=?", (name,)
        ).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def list_project_states() -> list[dict]:
    """All project states, ordered by most recently updated."""
    _ensure_project_state_table()
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM project_state ORDER BY updated_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def upsert_project_state(name: str, **fields) -> dict:
    """
    Partial update via read-merge-write. Only the provided fields are overwritten.
    fields: status, positioning, metrics(dict), risks(list), next_actions(list)
    Each metrics entry: {"value": ..., "as_of": "YYYY-MM-DD", "note": "..."(optional)}.
    """
    _ensure_project_state_table()
    existing = get_project_state(name) or {
        "name": name, "status": "active", "positioning": "",
        "metrics": {}, "risks": [], "next_actions": [],
    }

    for k in _PLAIN_FIELDS:
        if k in fields:
            existing[k] = fields[k]
    for k in _JSON_FIELDS:
        if k in fields:
            existing[k] = fields[k]

    now = datetime.now(KST).isoformat()
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO project_state "
            "(name, status, positioning, metrics_json, risks_json, next_actions_json, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                name,
                existing.get("status", "active"),
                existing.get("positioning", ""),
                json.dumps(existing.get("metrics", {}), ensure_ascii=False),
                json.dumps(existing.get("risks", []), ensure_ascii=False),
                json.dumps(existing.get("next_actions", []), ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
    existing["updated_at"] = now
    return existing


def render_state_for_prompt() -> str:
    """Markdown for the system prompt. Metrics include as_of and elapsed days to flag stale values."""
    states = list_project_states()
    # Only include projects with at least one of: positioning / metrics / next_actions
    meaningful = [
        s for s in states
        if s.get("positioning") or s.get("metrics") or s.get("next_actions")
    ]
    if not meaningful:
        return ""

    today = date.today()
    lines = ["아래 수치는 측정일(as of) 기준 과거값이다. 현재값으로 단정하지 말 것.\n"]
    for s in meaningful:
        head = f"### {s['name']}  (status: {s.get('status', '')}, updated {s.get('updated_at', '')[:10]})"
        lines.append(head)
        if s.get("positioning"):
            lines.append(f"positioning: {s['positioning']}")
        metrics = s.get("metrics") or {}
        if metrics:
            lines.append("metrics:")
            for key, m in metrics.items():
                if isinstance(m, dict):
                    val = m.get("value", "")
                    as_of = m.get("as_of", "")
                    note = f" — {m['note']}" if m.get("note") else ""
                    age = _days_ago(as_of, today)
                    lines.append(f"  - {key} = {val}  (as of {as_of}{age}){note}")
                else:
                    lines.append(f"  - {key} = {m}")
        risks = s.get("risks") or []
        if risks:
            lines.append("risks:")
            lines.extend(f"  - {r}" for r in risks)
        nexts = s.get("next_actions") or []
        if nexts:
            lines.append("next_actions:")
            lines.extend(f"  - {n}" for n in nexts)
        lines.append("")
    return "\n".join(lines).strip()


def _days_ago(as_of: str, today: date) -> str:
    """Elapsed-days string between as_of (YYYY-MM-DD) and today. Returns '' on parse failure."""
    try:
        d = date.fromisoformat(as_of)
        delta = (today - d).days
        if delta <= 0:
            return ""
        return f", {delta} days ago"
    except Exception:
        return ""


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for obj_key, json_key in _JSON_FIELDS.items():
        try:
            d[obj_key] = json.loads(d.pop(json_key) or ("{}" if obj_key == "metrics" else "[]"))
        except Exception:
            d[obj_key] = {} if obj_key == "metrics" else []
    return d


if __name__ == "__main__":
    seed_project_states()
    print(json.dumps(list_project_states(), ensure_ascii=False, indent=2))
