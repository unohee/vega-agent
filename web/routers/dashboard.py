# Created: 2026-05-27
# Purpose: Agent View dashboard + memory/brief/project/email/todo/widgets endpoints
# Previously in: web/server.py

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


# Things integration deferred — excluded from harness due to TCC hang in daemon context.
_THINGS_DISABLED = JSONResponse(
    {"ok": False, "error": "Things 통합은 현재 하네스에서 보류 상태입니다."},
    status_code=503,
)


@router.get("/api/dashboard")
async def dashboard_data():
    """Today's calendar events + Things Today + Linear In Progress."""
    loop = asyncio.get_event_loop()

    def _fetch():
        from pipeline.tools import calendar_list_events
        from pipeline.linear_client import list_issues
        from datetime import datetime
        import zoneinfo

        kst = zoneinfo.ZoneInfo("Asia/Seoul")
        now_kst = datetime.now(kst)
        today_str = now_kst.strftime("%Y-%m-%d")

        DAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
        try:
            raw_events = calendar_list_events(days_from_today=7, max_results=50)
            events = []
            for e in raw_events:
                start_raw = e.get("start", "")
                try:
                    if "T" in start_raw:
                        dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                        if dt.tzinfo:
                            dt = dt.astimezone(kst)
                        date_str = dt.strftime("%Y-%m-%d")
                        time_str = dt.strftime("%H:%M")
                        end_raw = ""
                        if e.get("end") and "T" in e["end"]:
                            dt_end = datetime.fromisoformat(e["end"].replace("Z", "+00:00"))
                            if dt_end.tzinfo:
                                dt_end = dt_end.astimezone(kst)
                            end_raw = dt_end.strftime("%H:%M")
                    else:
                        date_str = start_raw[:10]
                        time_str = "종일"
                        end_raw = ""
                except Exception:
                    continue
                day_label = DAY_KO[datetime.strptime(date_str, "%Y-%m-%d").weekday()]
                is_today = date_str == today_str
                events.append({
                    "title": e.get("summary", "(제목 없음)"),
                    "calendar": e.get("calendar", ""),
                    "date": date_str,
                    "day": day_label,
                    "is_today": is_today,
                    "start": time_str,
                    "end": end_raw,
                    "location": e.get("location", ""),
                })
        except Exception as ex:
            events = [{"title": f"캘린더 로드 실패: {ex}", "calendar": "", "date": today_str,
                       "day": "", "is_today": True, "start": "", "end": "", "location": ""}]

        todos = []

        try:
            raw_issues = list_issues(states=["In Progress"], limit=10)
            issues = [
                {
                    "identifier": i.get("identifier", ""),
                    "title": i.get("title", ""),
                    "state": i.get("state", ""),
                    "priority": i.get("priority", 0),
                    "labels": i.get("labels", []),
                    "project": i.get("project", ""),
                    "team": i.get("team", ""),
                }
                for i in raw_issues
            ]
        except Exception as ex:
            issues = [{"identifier": "", "title": f"Linear 로드 실패: {ex}",
                       "state": "", "priority": 0, "labels": []}]

        return {
            "date": now_kst.strftime("%Y년 %m월 %d일 %A").replace(
                "Monday", "월요일").replace("Tuesday", "화요일").replace("Wednesday", "수요일")
                .replace("Thursday", "목요일").replace("Friday", "금요일")
                .replace("Saturday", "토요일").replace("Sunday", "일요일"),
            "time": now_kst.strftime("%H:%M"),
            "events": events,
            "todos": todos,
            "issues": issues,
        }

    data = await loop.run_in_executor(None, _fetch)
    return JSONResponse(data)


@router.post("/api/todo/complete")
async def todo_complete(request: Request):
    """[Deferred] Things integration disabled."""
    return _THINGS_DISABLED


@router.get("/api/memory/recent")
async def memory_recent():
    """List of recent session narratives."""
    loop = asyncio.get_event_loop()
    def _fetch():
        from pipeline.heartbeat import get_recent_narratives
        return get_recent_narratives(limit=7)
    items = await loop.run_in_executor(None, _fetch)
    return JSONResponse({"memories": items})


@router.get("/api/briefs/recent")
async def briefs_recent():
    """List of recent daily briefs (morning/evening)."""
    loop = asyncio.get_event_loop()
    def _fetch():
        from pipeline.heartbeat import get_recent_briefs
        return get_recent_briefs(limit=4)
    items = await loop.run_in_executor(None, _fetch)
    return JSONResponse({"briefs": items})


@router.get("/api/projects/state")
async def projects_state():
    """Project state registry."""
    loop = asyncio.get_event_loop()
    def _fetch():
        from pipeline.project_state import list_project_states
        return list_project_states()
    items = await loop.run_in_executor(None, _fetch)
    return JSONResponse({"projects": items})


@router.get("/api/emails/priority")
async def email_priority():
    """Today's high/medium priority mail list."""
    loop = asyncio.get_event_loop()
    def _fetch():
        from pipeline.heartbeat import get_high_priority_today
        return get_high_priority_today(limit=8)
    items = await loop.run_in_executor(None, _fetch)
    return JSONResponse({"emails": items})


@router.get("/api/todos/suggest")
async def todos_suggest():
    """VEGA suggestions — calls the ChatGPT director without Things."""
    try:
        from pipeline.heartbeat import suggest_todos
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, suggest_todos, [])
        return {"ok": True, "suggestions": result}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/api/todos/suggest/accept")
async def todos_suggest_accept(request: Request):
    """[Deferred] Things integration disabled."""
    return _THINGS_DISABLED


@router.post("/api/todos/suggest/defer")
async def todos_suggest_defer(request: Request):
    """[Deferred] Things integration disabled."""
    return _THINGS_DISABLED
