# Created: 2026-05-18
# Purpose: DB query helpers for the VEGA agent — unified access to events/entities/persona
# Dependencies: sqlite3 (stdlib)
# Test Status: untested

import re
import sqlite3
from pathlib import Path

from pipeline.data_paths import db_path as _db_path
DB_PATH = _db_path()
_INITIAL_DB_PATH = DB_PATH


def _current_db_path() -> Path:
    # Preserve the historical DB_PATH monkeypatch hook, but do not pin normal runtime
    # callers to an import-time path when VEGA_DB_FILE/VEGA_DATA_DIR changes.
    if DB_PATH != _INITIAL_DB_PATH:
        return Path(DB_PATH)
    return _db_path()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_current_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_LEXICAL_TOKEN_RE = re.compile(r"[0-9A-Za-z_가-힣]+", re.UNICODE)
_LEXICAL_MAX_TOKENS = 8
_LEXICAL_TABLES = {
    "messages": {
        "fts": "messages_fts",
        "source_table": "messages",
        "columns": ("text",),
        "create": True,
    },
    "events": {
        "fts": "events_fts",
        "source_table": "events",
        "columns": ("title", "body", "tags"),
        "create": True,
    },
    "persona_sections": {
        "fts": "persona_sections_fts",
        "source_table": "persona_sections",
        "columns": ("section_key", "content", "notes", "source"),
        "create": True,
    },
    "entities": {
        "fts": "entities_fts",
        "source_table": "entities",
        "columns": ("name", "kind", "canonical_id", "aliases_json", "notes"),
        "create": True,
    },
}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_schema WHERE type IN ('table','view') AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_xinfo({_quote_ident(table)})").fetchall()}


def _ensure_lexical_fts(conn: sqlite3.Connection) -> None:
    """Create FTS5 mirrors for local memory tables that do not already own one."""
    for cfg in _LEXICAL_TABLES.values():
        if not cfg["create"]:
            continue
        table = cfg["source_table"]
        fts = cfg["fts"]
        columns = tuple(cfg["columns"])
        existing_cols = _table_columns(conn, table)
        required_cols = {"id", *columns}
        if not required_cols.issubset(existing_cols):
            # Legacy partial schemas are kept backward-compatible; skip FTS until migrated.
            continue

        was_missing = not _table_exists(conn, fts)
        column_sql = ", ".join(columns)
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {_quote_ident(fts)} "
            f"USING fts5({column_sql}, content={table!r}, content_rowid='id', tokenize='unicode61')"
        )
        _ensure_fts_triggers(conn, table, fts, columns)
        if was_missing:
            conn.execute(f"INSERT INTO {_quote_ident(fts)}({_quote_ident(fts)}) VALUES('rebuild')")


def _ensure_fts_triggers(conn: sqlite3.Connection, table: str, fts: str, columns: tuple[str, ...]) -> None:
    quoted_cols = ", ".join(_quote_ident(c) for c in columns)
    new_cols = ", ".join(f"new.{_quote_ident(c)}" for c in columns)
    old_cols = ", ".join(f"old.{_quote_ident(c)}" for c in columns)
    prefix = f"vega_{fts}"
    conn.execute(
        f"CREATE TRIGGER IF NOT EXISTS {_quote_ident(prefix + '_ai')} AFTER INSERT ON {_quote_ident(table)} BEGIN "
        f"INSERT INTO {_quote_ident(fts)}(rowid, {quoted_cols}) VALUES (new.id, {new_cols}); END"
    )
    conn.execute(
        f"CREATE TRIGGER IF NOT EXISTS {_quote_ident(prefix + '_ad')} AFTER DELETE ON {_quote_ident(table)} BEGIN "
        f"INSERT INTO {_quote_ident(fts)}({_quote_ident(fts)}, rowid, {quoted_cols}) "
        f"VALUES('delete', old.id, {old_cols}); END"
    )
    conn.execute(
        f"CREATE TRIGGER IF NOT EXISTS {_quote_ident(prefix + '_au')} AFTER UPDATE ON {_quote_ident(table)} BEGIN "
        f"INSERT INTO {_quote_ident(fts)}({_quote_ident(fts)}, rowid, {quoted_cols}) "
        f"VALUES('delete', old.id, {old_cols}); "
        f"INSERT INTO {_quote_ident(fts)}(rowid, {quoted_cols}) VALUES (new.id, {new_cols}); END"
    )


def build_fts5_prefix_query(query: str, max_tokens: int = _LEXICAL_MAX_TOKENS) -> str:
    """Build an FTS5 MATCH expression using token prefixes.

    unicode61 does not segment Korean morphologically; benchmarked retrieval uses prefix
    expansion (e.g. `\"알파\"*`) so Korean prefixes can match full eojeol tokens.
    """
    tokens = _LEXICAL_TOKEN_RE.findall((query or "").lower())
    if not tokens:
        return ""
    unique_tokens = list(dict.fromkeys(tokens))[:max_tokens]
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in unique_tokens)


def lexical_search(table: str, query: str, top_k: int = 5) -> list[dict]:
    """Ranked FTS5 lexical search over a configured table.

    Rows contain: source, table, id, text, snippet, bm25. Lower bm25 is better.
    """
    cfg = _LEXICAL_TABLES.get(table)
    if cfg is None:
        raise ValueError(f"unsupported lexical table: {table}")
    match = build_fts5_prefix_query(query)
    if not match or top_k <= 0:
        return []

    fts = cfg["fts"]
    with _conn() as conn:
        if not _table_exists(conn, fts):
            return []
        fts_cols = _table_columns(conn, fts)
        columns = [c for c in cfg["columns"] if c in fts_cols]
        if not columns:
            return []
        text_expr = " || ' ' || ".join(f"COALESCE({_quote_ident(c)}, '')" for c in columns)
        sql = f"""
            SELECT rowid AS id,
                   {text_expr} AS text,
                   snippet({_quote_ident(fts)}, 0, '<mark>', '</mark>', '…', 24) AS snippet,
                   bm25({_quote_ident(fts)}) AS bm25
            FROM {_quote_ident(fts)}
            WHERE {_quote_ident(fts)} MATCH ?
            ORDER BY bm25 ASC
            LIMIT ?
        """
        rows = conn.execute(sql, (match, int(top_k))).fetchall()
    return [
        {
            "source": fts,
            "table": cfg["source_table"],
            "id": row["id"],
            "text": row["text"] or "",
            "snippet": row["snippet"] or "",
            "bm25": row["bm25"],
        }
        for row in rows
    ]


def lexical_search_messages(query: str, top_k: int = 5) -> list[dict]:
    return lexical_search("messages", query, top_k)


def lexical_search_events(query: str, top_k: int = 5) -> list[dict]:
    return lexical_search("events", query, top_k)


def lexical_search_persona_sections(query: str, top_k: int = 5) -> list[dict]:
    return lexical_search("persona_sections", query, top_k)


def lexical_search_entities(query: str, top_k: int = 5) -> list[dict]:
    return lexical_search("entities", query, top_k)


def _ensure_schema() -> None:
    """Create persona/events/entities tables (idempotent). Safe for new user DBs.

    개인 VEGA 는 ingest 스크립트로 이 테이블들을 채우지만, vega-agent(빈 새 DB)는
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
                source      TEXT,
                version     INTEGER NOT NULL DEFAULT 1,
                is_active   INTEGER NOT NULL DEFAULT 1,
                notes       TEXT,
                updated_at  TEXT,
                ingested_at TEXT,
                user_edited INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date  TEXT NOT NULL,
                date_raw    TEXT,
                era         TEXT,
                title       TEXT NOT NULL,
                body        TEXT NOT NULL DEFAULT '',
                tags        TEXT,
                ingested_at TEXT,
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entity_edges (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                source_entity_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                relation_type    TEXT NOT NULL,
                evidence         TEXT,
                source_message_id TEXT,
                confidence       REAL NOT NULL DEFAULT 1.0,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (source_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
                FOREIGN KEY (target_entity_id) REFERENCES entities(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_edges_source ON entity_edges(source_entity_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_edges_target ON entity_edges(target_entity_id)"
        )
        # Migration: add columns to existing tables as schema evolves (idempotent)
        ps_cols = {r[1] for r in conn.execute("PRAGMA table_info(persona_sections)").fetchall()}
        if "user_edited" not in ps_cols:
            conn.execute("ALTER TABLE persona_sections ADD COLUMN user_edited INTEGER NOT NULL DEFAULT 0")
        if "source" not in ps_cols:
            conn.execute("ALTER TABLE persona_sections ADD COLUMN source TEXT")
        if "ingested_at" not in ps_cols:
            conn.execute("ALTER TABLE persona_sections ADD COLUMN ingested_at TEXT")

        ev_cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "date_raw" not in ev_cols:
            conn.execute("ALTER TABLE events ADD COLUMN date_raw TEXT")
        if "era" not in ev_cols:
            conn.execute("ALTER TABLE events ADD COLUMN era TEXT")
        if "ingested_at" not in ev_cols:
            conn.execute("ALTER TABLE events ADD COLUMN ingested_at TEXT")
        conn.execute(
            """CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                   title, body, tags,
                   content='events', content_rowid='id',
                   tokenize='unicode61'
               )"""
        )
        conn.execute(
            """INSERT INTO events_fts(events_fts)
                   VALUES('rebuild')"""
        )

        _ensure_lexical_fts(conn)


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


def _fts_query(keyword: str) -> str:
    terms = [t.replace('"', '""') for t in keyword.split() if t]
    return " OR ".join(f'"{t}"*' for t in terms)


def _rebuild_events_fts(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(title, body, tags, content='events', content_rowid='id', tokenize='unicode61')")
    conn.execute("INSERT INTO events_fts(events_fts) VALUES('rebuild')")


def search_events(keyword: str, limit: int = 20) -> list[dict]:
    """Full-text search across event title/body/tags, ranked by BM25 when FTS5 is available."""
    q = _fts_query(keyword)
    if not q:
        return []
    with _conn() as conn:
        try:
            _rebuild_events_fts(conn)
            rows = conn.execute(
                """SELECT ev.*, bm25(events_fts) AS rank
                   FROM events_fts
                   JOIN events ev ON ev.id = events_fts.rowid
                   WHERE events_fts MATCH ?
                   ORDER BY rank, ev.event_date DESC
                   LIMIT ?""",
                (q, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return [dict(r) for r in conn.execute(
                """SELECT * FROM events
                   WHERE title LIKE ? OR body LIKE ? OR tags LIKE ?
                   ORDER BY event_date DESC LIMIT ?""",
                (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit)
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


def entity_neighbors(entity_id: int) -> list[dict]:
    """List incoming and outgoing entity-edge neighbors for an entity id."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT
                edge.id AS edge_id,
                'outgoing' AS direction,
                edge.source_entity_id,
                edge.target_entity_id,
                edge.relation_type,
                edge.evidence,
                edge.source_message_id,
                edge.confidence,
                edge.created_at,
                edge.updated_at,
                neighbor.id AS neighbor_id,
                neighbor.name AS neighbor_name,
                neighbor.kind AS neighbor_kind,
                neighbor.canonical_id AS neighbor_canonical_id,
                neighbor.aliases_json AS neighbor_aliases_json,
                neighbor.notes AS neighbor_notes,
                neighbor.first_seen AS neighbor_first_seen,
                neighbor.last_seen AS neighbor_last_seen
            FROM entity_edges edge
            JOIN entities neighbor ON neighbor.id = edge.target_entity_id
            WHERE edge.source_entity_id = ?
            UNION ALL
            SELECT
                edge.id AS edge_id,
                'incoming' AS direction,
                edge.source_entity_id,
                edge.target_entity_id,
                edge.relation_type,
                edge.evidence,
                edge.source_message_id,
                edge.confidence,
                edge.created_at,
                edge.updated_at,
                neighbor.id AS neighbor_id,
                neighbor.name AS neighbor_name,
                neighbor.kind AS neighbor_kind,
                neighbor.canonical_id AS neighbor_canonical_id,
                neighbor.aliases_json AS neighbor_aliases_json,
                neighbor.notes AS neighbor_notes,
                neighbor.first_seen AS neighbor_first_seen,
                neighbor.last_seen AS neighbor_last_seen
            FROM entity_edges edge
            JOIN entities neighbor ON neighbor.id = edge.source_entity_id
            WHERE edge.target_entity_id = ?
            ORDER BY updated_at DESC, edge_id DESC
            """,
            (entity_id, entity_id),
        ).fetchall()
        return [dict(r) for r in rows]


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
        try:
            conn.execute(
                "INSERT INTO events_fts(rowid, title, body, tags) VALUES (?, ?, ?, ?)",
                (cur.lastrowid, title, body, tags),
            )
        except sqlite3.OperationalError:
            pass
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
