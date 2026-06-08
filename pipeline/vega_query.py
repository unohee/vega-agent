# Created: 2026-05-18
# Purpose: DB query helpers for the VEGA agent — unified access to events/entities/persona
# Dependencies: sqlite3 (stdlib)
# Test Status: untested

import sqlite3
from pathlib import Path

from pipeline.data_paths import db_path as _db_path
DB_PATH = _db_path()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema() -> None:
    """Create persona/events/entities tables (idempotent). Safe for new user DBs.

    개인 VEGA 는 ingest 스크립트로 이 테이블들을 채우지만, vega-core(빈 새 DB)는
    채널 봇만 띄워도 동작해야 한다. 테이블이 없으면 get_persona 등이 깨지므로
    모듈 로드 시 비어있는 스키마를 보장한다 (persona 가 비면 빈 문자열 반환).
    """
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS persona_sections (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                section_key TEXT NOT NULL,
                content     TEXT NOT NULL,
                scope       TEXT NOT NULL DEFAULT 'global',
                version     INTEGER NOT NULL DEFAULT 1,
                is_active   INTEGER NOT NULL DEFAULT 1,
                notes       TEXT,
                updated_at  TEXT,
                user_edited INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date  TEXT NOT NULL,
                title       TEXT NOT NULL,
                body        TEXT NOT NULL DEFAULT '',
                tags        TEXT,
                created_at  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                kind         TEXT,
                canonical_id TEXT,
                aliases_json TEXT,
                notes        TEXT,
                first_seen   TEXT,
                last_seen    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS event_entities (
                event_id   INTEGER NOT NULL,
                entity_id  INTEGER NOT NULL,
                match_text TEXT
            )
        """)
        # Migration: add user_edited to existing persona_sections (INT-1395 — Memory Inspector edit)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(persona_sections)").fetchall()}
        if "user_edited" not in cols:
            conn.execute("ALTER TABLE persona_sections ADD COLUMN user_edited INTEGER NOT NULL DEFAULT 0")


_ensure_schema()


# ── Event queries ─────────────────────────────────────────────────────────────

def events_by_date(start: str, end: str | None = None) -> list[dict]:
    """Query events by date range. start/end: YYYY-MM-DD or YYYY-MM"""
    sql = "SELECT * FROM events WHERE event_date >= ?"
    params: list = [start]
    if end:
        sql += " AND event_date <= ?"
        params.append(end)
    sql += " ORDER BY event_date"
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def events_by_tag(tag: str) -> list[dict]:
    """Filter events by tag (family/trading/research/business/mental_health/audio/ai_infra)"""
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM events WHERE tags LIKE ? ORDER BY event_date",
            (f"%{tag}%",)
        ).fetchall()]


def events_by_entity(name: str) -> list[dict]:
    """Query events linked to a person or organization by name"""
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT ev.* FROM events ev
               JOIN event_entities ee ON ee.event_id = ev.id
               JOIN entities e ON e.id = ee.entity_id
               WHERE e.name = ? OR e.canonical_id = ?
               ORDER BY ev.event_date""",
            (name, name)
        ).fetchall()]


def search_events(keyword: str, limit: int = 20) -> list[dict]:
    """Full-text search across event title and body"""
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT * FROM events
               WHERE title LIKE ? OR body LIKE ?
               ORDER BY event_date LIMIT ?""",
            (f"%{keyword}%", f"%{keyword}%", limit)
        ).fetchall()]


# ── Persona queries ────────────────────────────────────────────────────────────

def get_persona(section: str | None = None) -> str:
    """Return active persona_sections. If section=None, return all sections joined."""
    with _conn() as conn:
        if section:
            row = conn.execute(
                "SELECT content FROM persona_sections WHERE section_key=? AND is_active=1 ORDER BY version DESC LIMIT 1",
                (section,)
            ).fetchone()
            return row["content"] if row else ""
        rows = conn.execute(
            "SELECT section_key, content FROM persona_sections WHERE is_active=1 AND scope='global' ORDER BY id"
        ).fetchall()
        return "\n\n---\n\n".join(f"# {r['section_key']}\n{r['content']}" for r in rows)


# ── Entity queries ─────────────────────────────────────────────────────────────

def get_entity(name: str) -> dict | None:
    """Look up an entity by name"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM entities WHERE name=? OR canonical_id=? LIMIT 1",
            (name, name)
        ).fetchone()
        return dict(row) if row else None


def entity_timeline(name: str) -> list[dict]:
    """Event timeline for a person or organization (date + title + excerpt)"""
    events = events_by_entity(name)
    return [{"date": e["event_date"], "title": e["title"], "body": e["body"][:200]} for e in events]


# ── Context assembly ───────────────────────────────────────────────────────────

def context_for_date(date: str, window_days: int = 30) -> str:
    """Assemble events around a given date plus the identity persona into agent context."""
    from datetime import datetime, timedelta
    dt = datetime.fromisoformat(date)
    start = (dt - timedelta(days=window_days)).strftime("%Y-%m-%d")
    end = (dt + timedelta(days=window_days)).strftime("%Y-%m-%d")

    events = events_by_date(start, end)
    persona = get_persona("identity")

    lines = [f"## {date} 전후 {window_days}일 이벤트 ({len(events)}건)"]
    for ev in events:
        lines.append(f"- [{ev['event_date']}] {ev['title']}")

    lines.append("\n## 페르소나 (identity)")
    lines.append(persona[:800])

    return "\n".join(lines)


# ── Memory writes ─────────────────────────────────────────────────────────────

def persona_upsert(section_key: str, content: str, notes: str = "") -> dict:
    """
    Update a persona section.
    Deactivates the current active version and inserts a new one (version history preserved).
    Returns: {"section_key", "version", "ok": True}
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT version FROM persona_sections WHERE section_key=? AND is_active=1 ORDER BY version DESC LIMIT 1",
            (section_key,)
        ).fetchone()
        new_version = (row["version"] + 1) if row else 1
        conn.execute(
            "UPDATE persona_sections SET is_active=0 WHERE section_key=? AND is_active=1",
            (section_key,)
        )
        conn.execute(
            """INSERT INTO persona_sections
               (source, scope, section_key, content, version, is_active, ingested_at, user_edited, notes)
               VALUES ('vega', 'global', ?, ?, ?, 1, ?, 0, ?)""",
            (section_key, content, new_version, now, notes)
        )
    return {"section_key": section_key, "version": new_version, "ok": True}


def event_add(event_date: str, title: str, body: str, tags: str = "") -> dict:
    """
    Add a new event.
    event_date: YYYY-MM-DD, title: one-line summary, body: full content, tags: comma-separated
    Returns: {"id", "ok": True}
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO events (event_date, date_raw, era, title, body, tags, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event_date, event_date, event_date[:7], title, body, tags, now)
        )
        return {"id": cur.lastrowid, "ok": True}


def entity_upsert(name: str, kind: str, notes: str = "", aliases: list[str] | None = None) -> dict:
    """
    Add or update an entity.
    kind: 'person' | 'org' | 'project' | 'topic'
    Returns: {"id", "action": "created"|"updated", "ok": True}
    """
    import json as _json
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    aliases_json = _json.dumps(aliases or [], ensure_ascii=False)
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM entities WHERE name=? LIMIT 1", (name,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE entities SET kind=?, notes=?, aliases_json=?, last_seen=? WHERE id=?",
                (kind, notes, aliases_json, now[:10], row["id"])
            )
            return {"id": row["id"], "action": "updated", "ok": True}
        else:
            cur = conn.execute(
                """INSERT INTO entities (kind, name, notes, aliases_json, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (kind, name, notes, aliases_json, now[:10], now[:10])
            )
            return {"id": cur.lastrowid, "action": "created", "ok": True}


if __name__ == "__main__":
    # Quick smoke test
    print("=== 2026-03 events ===")
    for ev in events_by_date("2026-03-01", "2026-03-31"):
        print(f"  {ev['event_date']} | {ev['title'][:60]}")

    print("\n=== mental_health event count ===")
    print(f"  {len(events_by_tag('mental_health'))} events")

    print("\n=== entity timeline — last 5 events ===")
    for e in entity_timeline("이슬")[-5:]:
        print(f"  {e['date']} | {e['title'][:50]}")

    print("\n=== persona identity (first 300 chars) ===")
    print(get_persona("identity")[:300])
