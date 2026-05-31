# Created: 2026-05-27
# Purpose: Memory Inspector API — persona/events/entities CRUD (RES-225)
# Dependencies: pipeline/data_paths.py, sqlite3

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


def _db() -> Path:
    try:
        from pipeline.data_paths import db_path
        return db_path()
    except Exception:
        return Path.home() / "Library/Application Support/VEGA/agent.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db()))
    conn.row_factory = sqlite3.Row
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
async def delete_event(event_id: int):
    """Delete an event (hard delete — events can be re-ingested)."""
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


@router.patch("/api/memory/entities/{entity_id}")
async def update_entity(entity_id: int, payload: EntityUpdate):
    """Edit entity notes and aliases."""
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
        if not updates:
            return JSONResponse({"ok": False, "error": "no fields to update"}, status_code=400)
        params.append(entity_id)
        conn.execute(f"UPDATE entities SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
    return JSONResponse({"ok": True, "id": entity_id})


@router.delete("/api/memory/entities/{entity_id}")
async def delete_entity(entity_id: int):
    """Delete an entity. Hard delete instead of deactivating is_active (re-ingestable)."""
    with _conn() as conn:
        r = conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))
        conn.commit()
        if r.rowcount == 0:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, "deleted_id": entity_id})


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
    return JSONResponse({
        "persona": {"total": persona_total, "active": persona_active},
        "events": {"total": events_total},
        "entities": {"total": entities_total, "by_kind": [dict(r) for r in entity_kinds]},
    })
