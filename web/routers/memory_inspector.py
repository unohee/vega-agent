# Created: 2026-05-27
# Purpose: Memory Inspector API — persona/events/entities CRUD (RES-225)
# Dependencies: pipeline/data_paths.py, sqlite3

from __future__ import annotations

import sqlite3
import re
from pathlib import Path
from typing import Optional

try:
    from fastapi import APIRouter, Query, Request
    from fastapi.responses import JSONResponse
except ModuleNotFoundError:
    class APIRouter:
        def get(self, *_args, **_kwargs):
            return lambda fn: fn

        def patch(self, *_args, **_kwargs):
            return lambda fn: fn

        def post(self, *_args, **_kwargs):
            return lambda fn: fn

        def delete(self, *_args, **_kwargs):
            return lambda fn: fn

    def Query(default, *_args, **_kwargs):
        return default

    class Request:
        pass

    class JSONResponse:
        def __init__(self, content, status_code=200):
            import json

            self.status_code = status_code
            self.body = json.dumps(content).encode("utf-8")

from pydantic import BaseModel

router = APIRouter()


_LEXICAL_TOKEN_RE = re.compile(r"[0-9A-Za-z_가-힣]+", re.UNICODE)
_SEARCH_FTS_TABLES = {
    "persona_sections": ("persona_sections_fts", ("section_key", "content", "notes", "source")),
    "events": ("events_fts", ("title", "body", "tags")),
    "entities": ("entities_fts", ("name", "kind", "canonical_id", "aliases_json", "notes")),
}


def _build_fts5_prefix_query(query: str, max_tokens: int = 8) -> str:
    try:
        from pipeline import vega_query
        return vega_query.build_fts5_prefix_query(query, max_tokens=max_tokens)
    except (ImportError, sqlite3.Error):
        tokens = _LEXICAL_TOKEN_RE.findall((query or "").lower())
        unique_tokens = list(dict.fromkeys(tokens))[:max_tokens]
        return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in unique_tokens)


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


def _where_clause(conditions: list[str]) -> str:
    return f" WHERE {' AND '.join(conditions)}" if conditions else ""


def _like_condition(alias: str, columns: tuple[str, ...], params: list, search: str) -> str:
    pattern = f"%{search}%"
    params.extend([pattern] * len(columns))
    return "(" + " OR ".join(f"COALESCE({alias}.{_quote_ident(col)}, '') LIKE ?" for col in columns) + ")"


def _ensure_search_indexes(conn: sqlite3.Connection) -> None:
    """Inspector 검색용 FTS5 미러를 보장한다."""
    for table, (fts, desired_columns) in _SEARCH_FTS_TABLES.items():
        table_cols = _table_columns(conn, table)
        if "id" not in table_cols:
            continue
        columns = tuple(col for col in desired_columns if col in table_cols)
        if not columns:
            continue
        try:
            was_missing = not _table_exists(conn, fts)
            column_sql = ", ".join(_quote_ident(col) for col in columns)
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {_quote_ident(fts)} "
                f"USING fts5({column_sql}, content={table!r}, content_rowid='id', tokenize='unicode61')"
            )
            _ensure_search_triggers(conn, table, fts, columns)
            if was_missing:
                conn.execute(f"INSERT INTO {_quote_ident(fts)}({_quote_ident(fts)}) VALUES('rebuild')")
        except sqlite3.OperationalError:
            continue


def _ensure_search_triggers(
    conn: sqlite3.Connection,
    table: str,
    fts: str,
    columns: tuple[str, ...],
) -> None:
    quoted_cols = ", ".join(_quote_ident(c) for c in columns)
    new_cols = ", ".join(f"new.{_quote_ident(c)}" for c in columns)
    old_cols = ", ".join(f"old.{_quote_ident(c)}" for c in columns)
    prefix = f"vega_inspector_{fts}"
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


def _db() -> Path:
    try:
        from pipeline.data_paths import db_path
        return db_path()
    except Exception:
        return Path.home() / "Library/Application Support/VEGA/agent.db"


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create persona_sections/events/entities if missing (fresh/partial DB).
    IF NOT EXISTS preserves existing data/schema. Lets Memory Inspector return
    empty lists instead of 500 on a fresh DB (INT-1395)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS persona_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, section_key TEXT NOT NULL,
            content TEXT NOT NULL, scope TEXT DEFAULT 'global', version INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1, notes TEXT, updated_at TEXT, user_edited INTEGER DEFAULT 0,
            sensitivity TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_date TEXT NOT NULL,
            title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', tags TEXT, created_at TEXT,
            sensitivity TEXT
        );
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT,
            canonical_id TEXT, aliases_json TEXT, notes TEXT, first_seen TEXT, last_seen TEXT,
            sensitivity TEXT
        );
        CREATE TABLE IF NOT EXISTS entity_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity_id INTEGER NOT NULL,
            target_entity_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            evidence TEXT,
            source_message_id TEXT,
            confidence REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (source_entity_id) REFERENCES entities(id) ON DELETE CASCADE,
            FOREIGN KEY (target_entity_id) REFERENCES entities(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_entity_edges_source ON entity_edges(source_entity_id);
        CREATE INDEX IF NOT EXISTS idx_entity_edges_target ON entity_edges(target_entity_id);
        CREATE TABLE IF NOT EXISTS event_entities (
            event_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            match_text TEXT
        );
    """)
    # 민감도 태그 마이그레이션 (INT-1404)
    for tbl in ("persona_sections", "events", "entities"):
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            if "sensitivity" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN sensitivity TEXT")
        except Exception:
            pass
    _ensure_search_indexes(conn)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_tables(conn)
    return conn


# ── Persona Sections ─────────────────────────────────────────────

@router.get("/api/memory/persona")
async def list_persona(
    active_only: bool = Query(False),
    search: str = Query(""),
):
    """List persona sections."""
    with _conn() as conn:
        base_query = "SELECT p.* FROM persona_sections p"
        count_query = "SELECT count(*) FROM persona_sections p"
        joins = []
        conditions: list[str] = []
        params: list = []
        order_by = "ORDER BY p.scope, p.section_key"
        if search:
            fts_query = _build_fts5_prefix_query(search)
            if fts_query and _table_exists(conn, "persona_sections_fts"):
                joins.append("JOIN persona_sections_fts ON p.id = persona_sections_fts.rowid")
                conditions.append("persona_sections_fts MATCH ?")
                params.append(fts_query)
                order_by = "ORDER BY bm25(persona_sections_fts), p.scope, p.section_key"
            else:
                conditions.append(_like_condition("p", ("section_key", "content", "notes"), params, search))
        if active_only:
            conditions.append("p.is_active = 1")
        join_sql = " " + " ".join(joins) if joins else ""
        where = _where_clause(conditions)
        total_query = count_query + join_sql + where
        total = conn.execute(total_query, params).fetchone()[0]
        rows = conn.execute(
            f"{base_query}{join_sql}{where} {order_by}",
            params
        ).fetchall()
    return JSONResponse({"rows": [dict(r) for r in rows], "count": len(rows)})


class PersonaUpdate(BaseModel):
    content: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None
    sensitivity: Optional[str] = None  # family/finance/dispute/health etc. — empty clears (INT-1404)


@router.patch("/api/memory/persona/{section_id}")
async def update_persona(section_id: int, payload: PersonaUpdate):
    """Edit a persona section."""
    with _conn() as conn:
        row = conn.execute("SELECT id FROM persona_sections WHERE id=?", (section_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        updates = []
        params = []
        if payload.content is not None:
            updates.append("content=?"); params.append(payload.content)
        if payload.is_active is not None:
            updates.append("is_active=?"); params.append(1 if payload.is_active else 0)
        if payload.notes is not None:
            updates.append("notes=?"); params.append(payload.notes)
        if payload.sensitivity is not None:
            updates.append("sensitivity=?"); params.append(payload.sensitivity or None)
        if not updates:
            return JSONResponse({"ok": False, "error": "no fields to update"}, status_code=400)
        updates.append("user_edited=1")
        params.append(section_id)
        conn.execute(f"UPDATE persona_sections SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
    return JSONResponse({"ok": True, "id": section_id})


# ── Events ───────────────────────────────────────────────────────

@router.get("/api/memory/events")
async def list_events(
    search: str = Query(""),
    tag: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List events."""
    with _conn() as conn:
        base_query = "SELECT e.* FROM events e"
        count_query = "SELECT count(*) FROM events e"
        joins = []
        conditions: list[str] = []
        params: list = []
        order_by = "ORDER BY e.event_date DESC, e.id DESC"
        fts_conditions = []
        search_q = _build_fts5_prefix_query(search) if search else ""
        tag_q = _build_fts5_prefix_query(tag) if tag else ""
        if search_q:
            fts_conditions.append(f"({search_q})")
        if tag_q:
            fts_conditions.append(f"tags : ({tag_q})")
        if fts_conditions and _table_exists(conn, "events_fts"):
            joins.append("JOIN events_fts ON e.id = events_fts.rowid")
            conditions.append("events_fts MATCH ?")
            params.append(" AND ".join(fts_conditions))
            order_by = "ORDER BY bm25(events_fts), e.event_date DESC, e.id DESC"
        else:
            if search:
                conditions.append(_like_condition("e", ("title", "body", "tags"), params, search))
            if tag:
                conditions.append(_like_condition("e", ("tags",), params, tag))
        join_sql = " " + " ".join(joins) if joins else ""
        where = _where_clause(conditions)
        total_query = count_query + join_sql + where
        total = conn.execute(total_query, params).fetchone()[0]
        rows = conn.execute(
            f"{base_query}{join_sql}{where} {order_by} LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
    return JSONResponse({"rows": [dict(r) for r in rows], "total": total, "offset": offset})


@router.delete("/api/memory/events/{event_id}")
async def delete_event(event_id: int, request: Request):
    """Delete an event (hard delete — events can be re-ingested)."""
    from web.state import is_loopback as _is_loopback
    if not _is_loopback(request):
        return JSONResponse({"error": "로컬 앱에서만 삭제할 수 있습니다."}, status_code=403)
    with _conn() as conn:
        r = conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        conn.commit()
        if r.rowcount == 0:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, "deleted_id": event_id})


# ── Entities ─────────────────────────────────────────────────────

@router.get("/api/memory/entities")
async def list_entities(
    kind: str = Query(""),
    search: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List entities."""
    with _conn() as conn:
        conditions: list[str] = []
        params: list = []
        select_query = "SELECT e.*"
        base_query = " FROM entities e"
        count_query = "SELECT count(*) FROM entities e"
        joins = []
        order_params: list = []
        order_by = "ORDER BY e.last_seen DESC, e.id DESC"
        if search:
            fts_query = _build_fts5_prefix_query(search)
            if fts_query and _table_exists(conn, "entities_fts"):
                select_query += ", bm25(entities_fts) AS search_rank"
                joins.append("JOIN entities_fts ON e.id = entities_fts.rowid")
                conditions.append("entities_fts MATCH ?")
                params.append(fts_query)
            else:
                select_query += ", 0.0 AS search_rank"
                conditions.append(_like_condition("e", ("name", "kind", "canonical_id", "aliases_json", "notes"), params, search))
            order_params = [
                search,
                search,
                f'%"{search.lower()}"%',
                f"{search}%",
            ]
            order_by = (
                "ORDER BY CASE "
                "WHEN lower(e.name) = lower(?) THEN 0 "
                "WHEN lower(COALESCE(e.canonical_id, '')) = lower(?) THEN 1 "
                "WHEN lower(COALESCE(e.aliases_json, '')) LIKE ? THEN 2 "
                "WHEN lower(e.name) LIKE lower(?) THEN 3 "
                "ELSE 4 END, search_rank, e.last_seen DESC, e.id DESC"
            )
        if kind:
            conditions.append("e.kind=?")
            params.append(kind)
        join_sql = " " + " ".join(joins) if joins else ""
        where = _where_clause(conditions)
        total_query = count_query + join_sql + where
        total = conn.execute(total_query, params).fetchone()[0]
        rows = conn.execute(
            f"{select_query}{base_query}{join_sql}{where} {order_by} LIMIT ? OFFSET ?",
            params + order_params + [limit, offset]
        ).fetchall()
    return JSONResponse({"rows": [dict(r) for r in rows], "total": total})


class EntityUpdate(BaseModel):
    notes: Optional[str] = None
    aliases_json: Optional[str] = None
    sensitivity: Optional[str] = None  # 민감도 태그 (INT-1404)


@router.patch("/api/memory/entities/{entity_id}")
async def update_entity(entity_id: int, payload: EntityUpdate):
    """Edit entity notes, aliases, sensitivity."""
    with _conn() as conn:
        row = conn.execute("SELECT id FROM entities WHERE id=?", (entity_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        updates = []
        params = []
        if payload.notes is not None:
            updates.append("notes=?"); params.append(payload.notes)
        if payload.aliases_json is not None:
            updates.append("aliases_json=?"); params.append(payload.aliases_json)
        if payload.sensitivity is not None:
            updates.append("sensitivity=?"); params.append(payload.sensitivity or None)
        if not updates:
            return JSONResponse({"ok": False, "error": "no fields to update"}, status_code=400)
        params.append(entity_id)
        conn.execute(f"UPDATE entities SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
    return JSONResponse({"ok": True, "id": entity_id})


# ── Sensitive items bulk view (INT-1404) ─────────────────────────
@router.get("/api/memory/sensitive")
async def list_sensitive():
    """Items tagged with sensitivity, across persona/events/entities."""
    out = {"persona": [], "events": [], "entities": []}
    with _conn() as conn:
        for tbl, key in (("persona_sections", "persona"), ("events", "events"), ("entities", "entities")):
            try:
                rows = conn.execute(
                    f"SELECT * FROM {tbl} WHERE sensitivity IS NOT NULL AND sensitivity != ''"
                ).fetchall()
                out[key] = [dict(r) for r in rows]
            except Exception:
                pass
    total = sum(len(v) for v in out.values())
    return JSONResponse({**out, "total": total})


@router.delete("/api/memory/entities/{entity_id}")
async def delete_entity(entity_id: int, request: Request):
    """Delete an entity. Hard delete instead of deactivating is_active (re-ingestable)."""
    from web.state import is_loopback as _is_loopback
    if not _is_loopback(request):
        return JSONResponse({"error": "로컬 앱에서만 삭제할 수 있습니다."}, status_code=403)
    with _conn() as conn:
        r = conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))
        conn.commit()
        if r.rowcount == 0:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, "deleted_id": entity_id})


# ── Rules & Skills (에이전트 자기진화 산물) ──────────────────────

@router.get("/api/memory/rules")
async def list_rules():
    """저장된 행동 규칙 목록 (RULES.md)."""
    try:
        from pipeline.tools import _rule_list
        return JSONResponse(_rule_list())
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "rules": []}, status_code=500)


# ── Summary ──────────────────────────────────────────────────────

CANONICAL_ID_BASELINE_COVERAGE_PERCENT = 3.4


def _canonical_id_coverage_report(conn: sqlite3.Connection) -> dict:
    total_entities = conn.execute("SELECT count(*) FROM entities").fetchone()[0]
    canonicalized_entities = conn.execute(
        "SELECT count(*) FROM entities WHERE canonical_id IS NOT NULL AND trim(canonical_id) != ''"
    ).fetchone()[0]
    current_coverage = round((canonicalized_entities / total_entities) * 100, 1) if total_entities else 0.0

    rows = conn.execute(
        """
        SELECT id, name, kind, trim(canonical_id) AS canonical_id
        FROM entities
        WHERE canonical_id IS NOT NULL AND trim(canonical_id) != ''
        ORDER BY canonical_id, name, id
        """
    ).fetchall()
    clusters_by_canonical: dict[str, list[dict]] = {}
    for row in rows:
        clusters_by_canonical.setdefault(row["canonical_id"], []).append({
            "id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
        })
    duplicate_clusters = [
        {"canonical_id": canonical_id, "count": len(entities), "entities": entities}
        for canonical_id, entities in clusters_by_canonical.items()
        if len(entities) > 1
    ]
    duplicate_clusters.sort(key=lambda c: (-c["count"], c["canonical_id"]))

    unresolved_rows = conn.execute(
        """
        SELECT e.id, e.name, e.kind,
               count(DISTINCT ee.event_id) AS event_count,
               count(*) AS mention_count
        FROM entities e
        JOIN event_entities ee ON ee.entity_id = e.id
        WHERE e.canonical_id IS NULL OR trim(e.canonical_id) = ''
        GROUP BY e.id, e.name, e.kind
        ORDER BY event_count DESC, mention_count DESC, lower(e.name), e.id
        LIMIT 20
        """
    ).fetchall()

    return {
        "baseline_coverage_percent": CANONICAL_ID_BASELINE_COVERAGE_PERCENT,
        "current_coverage_percent": current_coverage,
        "post_run_coverage_percent": current_coverage,
        "total_entities": total_entities,
        "canonicalized_entities": canonicalized_entities,
        "duplicate_canonical_clusters": {
            "total": len(duplicate_clusters),
            "clusters": duplicate_clusters[:20],
        },
        "unresolved_high_frequency_entities": [dict(row) for row in unresolved_rows],
    }


@router.get("/api/memory/entity-resolution")
async def entity_resolution_report():
    """Entity-resolution coverage and review queues. No implicit merges are performed."""
    with _conn() as conn:
        report = _canonical_id_coverage_report(conn)
    return JSONResponse(report)


@router.get("/api/memory/summary")
async def memory_summary():
    """Overall memory status summary."""
    with _conn() as conn:
        persona_total = conn.execute("SELECT count(*) FROM persona_sections").fetchone()[0]
        persona_active = conn.execute("SELECT count(*) FROM persona_sections WHERE is_active=1").fetchone()[0]
        events_total = conn.execute("SELECT count(*) FROM events").fetchone()[0]
        entities_total = conn.execute("SELECT count(*) FROM entities").fetchone()[0]
        entity_kinds = conn.execute(
            "SELECT kind, count(*) as cnt FROM entities GROUP BY kind ORDER BY cnt DESC"
        ).fetchall()
        entity_resolution = _canonical_id_coverage_report(conn)
        # 세션 메모리(narrative) 카운트도 — 테이블 없을 수 있어 방어적
        try:
            sessions_total = conn.execute("SELECT count(*) FROM session_digest").fetchone()[0]
        except Exception:
            sessions_total = 0
    return JSONResponse({
        "persona": {"total": persona_total, "active": persona_active},
        "events": {"total": events_total},
        "entities": {
            "total": entities_total,
            "by_kind": [dict(r) for r in entity_kinds],
            "entity_resolution": entity_resolution,
        },
        "entity_resolution": entity_resolution,
        "sessions": {"total": sessions_total},
    })


# ── Memory & Context 설정 ─────────────────────────────────────────
# compaction 임계값·보존 메시지 수·메모리 자동 업데이트 on/off.
# 저장소: VEGA_DATA_DIR/memory_settings.json (compaction.load/save_memory_settings).
# settings.html Memory & Context 패널이 GET/POST로 사용 (INT-1473 버그2).

@router.get("/api/memory/settings")
async def memory_settings_get():
    from pipeline.compaction import load_memory_settings, _SETTINGS_DEFAULTS
    return JSONResponse({"settings": load_memory_settings(), "defaults": _SETTINGS_DEFAULTS})


@router.post("/api/memory/settings")
async def memory_settings_set(request: Request):
    from pipeline.compaction import save_memory_settings
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    try:
        saved = save_memory_settings(body or {})
        return JSONResponse({"ok": True, "settings": saved})
    except (ValueError, TypeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
