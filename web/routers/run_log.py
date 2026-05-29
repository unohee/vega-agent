# Created: 2026-05-27
# Purpose: Run Log API — query tool execution history (RES-224)
# Dependencies: pipeline/run_log.py

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/run-log")
async def get_run_log(
    session_uuid: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Query tool execution log. Filtered to the given session if session_uuid is provided."""
    from pipeline.run_log import get_session_log, get_recent_log
    if session_uuid:
        rows = get_session_log(session_uuid, limit=limit)
    else:
        rows = get_recent_log(limit=limit)
    return JSONResponse({"rows": rows, "count": len(rows)})
