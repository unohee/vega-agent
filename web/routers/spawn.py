# Created: 2026-06-21
# Purpose: Spawn tree API — live sub-agent activity for the current turn.
# Dependencies: pipeline/spawn.py

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/spawn/tree")
async def spawn_tree(
    session_uuid: str | None = Query(None),
    include_done: bool = Query(True),
):
    """현재 세션 또는 전체 세션의 sub-agent tree snapshot."""
    from pipeline.spawn import active_count, list_tree

    agents = list_tree(session_uuid, include_done=include_done)
    return JSONResponse({
        "agents": agents,
        "count": len(agents),
        "active": active_count(session_uuid),
    })
