# Created: 2026-05-27
# Purpose: Memory Inspector API — persona/events/entities CRUD (RES-225)
# Dependencies: pipeline/data_paths.py, sqlite3

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


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
    """)
    # 민감도 태그 마이그레이션 (INT-1404)
    for tbl in ("persona_sections", "events", "entities"):
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            if "sensitivity" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN sensitivity TEXT")
        except Exception:
            pass


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db()))
    conn.row_factory = sqlite3.Row
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
        sql = "SELECT * FROM persona_sections"
        params: list = []
        conditions = []
        if active_only:
            conditions.append("is_active = 1")
        if search:
            conditions.append("(content LIKE ? OR section_key LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY scope, section_key"
        rows = conn.execute(sql, params).fetchall()
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
        conditions = []
        params: list = []
        if search:
            conditions.append("(title LIKE ? OR body LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f"%{tag}%")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        total = conn.execute(f"SELECT count(*) FROM events {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY event_date DESC, id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
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
        conditions = []
        params: list = []
        if kind:
            conditions.append("kind=?"); params.append(kind)
        if search:
            conditions.append("(name LIKE ? OR notes LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        total = conn.execute(f"SELECT count(*) FROM entities {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM entities {where} ORDER BY last_seen DESC, id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
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
        # 세션 메모리(narrative) 카운트도 — 테이블 없을 수 있어 방어적
        try:
            sessions_total = conn.execute("SELECT count(*) FROM session_digest").fetchone()[0]
        except Exception:
            sessions_total = 0
    return JSONResponse({
        "persona": {"total": persona_total, "active": persona_active},
        "events": {"total": events_total},
        "entities": {"total": entities_total, "by_kind": [dict(r) for r in entity_kinds]},
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
