# Created: 2026-05-27
# Purpose: Slash command metadata + Agent View custom widget endpoints
# Previously in: web/server.py (lines 744-910)

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from pipeline.session_store import list_sessions

router = APIRouter()


# Built-in slash commands (autocomplete metadata)
_BUILTIN_COMMANDS = [
    {"name": "events", "hint": "<날짜>", "desc": "날짜 범위 이벤트"},
    {"name": "who", "hint": "<이름>", "desc": "인물/조직 프로필 + 타임라인"},
    {"name": "tag", "hint": "<태그>", "desc": "태그별 이벤트"},
    {"name": "search", "hint": "<키워드>", "desc": "키워드 전문 검색"},
    {"name": "context", "hint": "<날짜>", "desc": "날짜 전후 컨텍스트"},
    {"name": "persona", "hint": "[섹션]", "desc": "페르소나 섹션 조회"},
    {"name": "sessions", "hint": "", "desc": "저장된 세션 목록"},
    {"name": "resume", "hint": "<id>", "desc": "이전 세션 복원"},
    {"name": "new", "hint": "[제목]", "desc": "새 세션 시작"},
    {"name": "rename", "hint": "<제목>", "desc": "현재 세션 이름 변경"},
    {"name": "plan", "hint": "[요구사항]", "desc": "Plan 모드 — 실행 도구 차단, 계획만 세움"},
    {"name": "plan-off", "hint": "", "desc": "Plan 모드 해제"},
    {"name": "rules", "hint": "", "desc": "저장된 행동 규칙 목록 보기"},
    {"name": "audit", "hint": "", "desc": "도구 텔레메트리 — 호출/실패 통계"},
    {"name": "research", "hint": "[주제]", "desc": "Research 모드 — 웹검색 우선, 가설→근거→결론, 출처 명시"},
    {"name": "research-off", "hint": "", "desc": "Research 모드 해제"},
    {"name": "yolo", "hint": "", "desc": "YOLO 모드 — host_exec 자동 승인 (하드 차단·시크릿은 그대로)"},
    {"name": "yolo-off", "hint": "", "desc": "YOLO 모드 해제"},
    {"name": "help", "hint": "", "desc": "도움말"},
]


@router.get("/api/commands")
async def list_commands():
    """Slash command list (built-in + custom) — for UI autocomplete."""
    out = list(_BUILTIN_COMMANDS)
    try:
        from pipeline.commands import load_commands
        for c in load_commands().values():
            out.append({"name": c.name, "hint": c.argument_hint, "desc": c.description, "custom": True})
    except Exception:
        pass
    return JSONResponse({"commands": out})


# ── Agent View custom widgets ─────────────────────────────────────────────────
from pipeline.data_paths import widgets_path as _widgets_path_fn
_WIDGETS_PATH = _widgets_path_fn()


@router.get("/api/widgets")
async def list_widgets():
    """Return widget specs from data/widgets.json (hot-reloaded on each request)."""
    if not _WIDGETS_PATH.exists():
        return JSONResponse({"widgets": []})
    try:
        data = json.loads(_WIDGETS_PATH.read_text(encoding="utf-8"))
        widgets = [w for w in (data.get("widgets") or []) if isinstance(w, dict)]
        return JSONResponse({"widgets": widgets})
    except Exception as e:
        return JSONResponse({"widgets": [], "error": str(e)})


def _ds_clock() -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    return {"value": now.strftime("%H:%M"), "label": now.strftime("%Y-%m-%d (%a)")}


def _ds_session_count() -> dict:
    try:
        n = len(list_sessions(limit=250))
    except Exception:
        n = 0
    return {"value": str(n), "label": "개 세션"}


def _ds_recent_command() -> dict:
    try:
        from pipeline.commands import load_commands
        cmds = list(load_commands().values())
        if cmds:
            last = cmds[-1]
            return {"value": f"/{last.name}", "label": last.description[:40] or "최근 skill"}
    except Exception:
        pass
    return {"value": "—", "label": "커스텀 skill 없음"}



def _ds_mail_count() -> dict:
    try:
        from pipeline.heartbeat import get_high_priority_today
        items = get_high_priority_today(limit=20)
        return {"value": str(len(items)), "label": "중요 메일",
                "items": [{"title": (m.get("subject") or m.get("title") or "")[:60],
                           "sub": m.get("from") or m.get("sender") or ""} for m in items[:8]]}
    except Exception:
        return {"value": "—", "label": "메일"}


def _ds_today_brief() -> dict:
    """Most recent daily brief body (for text widgets)."""
    try:
        from pipeline.heartbeat import get_recent_briefs
        briefs = get_recent_briefs(limit=1)
        if briefs:
            b = briefs[0]
            return {"text": (b.get("body") or b.get("text") or "")[:1500]}
    except Exception:
        pass
    return {"text": "아직 생성된 브리핑이 없습니다."}


def _ds_calendar_today() -> dict:
    """Today's Google Calendar events."""
    try:
        from pipeline.tools_google import calendar_list_events
        events = calendar_list_events(days_from_today=1, max_results=10)
        return {
            "value": str(len(events)),
            "label": "오늘 일정",
            "items": [
                {
                    "title": e.get("summary") or "(제목 없음)",
                    "sub": (e.get("start") or "")[:16],
                }
                for e in events[:8]
            ],
        }
    except Exception:
        return {"value": "—", "label": "일정"}


def _ds_project_count() -> dict:
    try:
        from pipeline.project_state import list_project_states
        items = list_project_states()
        return {"value": str(len(items)), "label": "추적 프로젝트",
                "items": [{"title": p.get("name") or p.get("project") or "",
                           "sub": p.get("status") or ""} for p in items[:8]]}
    except Exception:
        return {"value": "—", "label": "프로젝트"}


def _ds_skill_count() -> dict:
    try:
        from pipeline.commands import load_commands
        cmds = list(load_commands().values())
        return {"value": str(len(cmds)), "label": "커스텀 skill",
                "items": [{"title": f"/{c.name}", "sub": c.description[:50]} for c in cmds[:8]]}
    except Exception:
        return {"value": "—", "label": "skill"}


# Whitelist: only these data sources may be called by widgets (prevents arbitrary code execution)
# git_status excluded — exposes local paths and changed file lists, runs subprocess
_WIDGET_SOURCES = {
    "clock": _ds_clock,
    "session_count": _ds_session_count,
    "recent_command": _ds_recent_command,
    "mail_count": _ds_mail_count,
    "today_brief": _ds_today_brief,
    "calendar_today": _ds_calendar_today,
    "project_count": _ds_project_count,
    "skill_count": _ds_skill_count,
}


@router.get("/api/widgets/data/{source}")
async def widget_data(source: str):
    """Widget data source — only whitelisted handlers are callable."""
    fn = _WIDGET_SOURCES.get(source)
    if not fn:
        return JSONResponse({"error": f"unknown data source: {source}"}, status_code=400)
    try:
        return JSONResponse(fn())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Action widget execution — invokes slash commands in an isolated context ────

import asyncio  # noqa: E402
import re as _re  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402


def _find_widget(widget_id: str) -> dict | None:
    if not _WIDGETS_PATH.exists():
        return None
    try:
        data = json.loads(_WIDGETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    for w in data.get("widgets") or []:
        if isinstance(w, dict) and w.get("id") == widget_id:
            return w
    return None


def _render_action_prompt(widget: dict, values: dict) -> str:
    """Substitute widget skill body with input values to build the LLM prompt."""
    from pipeline.commands import get_command, expand_command

    cmd = get_command(widget["skill"])
    if cmd is None:
        raise ValueError(f"slash command '/{widget['skill']}' not found")
    args_parts = []
    for inp in widget.get("inputs") or []:
        name = inp["name"]
        v = values.get(name, "")
        if isinstance(v, (int, float)):
            v = str(v)
        v = (v or "").strip()
        if inp.get("required", True) and not v:
            raise ValueError(f"required input missing: {inp.get('label') or name}")
        args_parts.append(f"{name}={v}")
    args = " ".join(args_parts)

    body = cmd.body
    has_placeholder = "$ARGUMENTS" in body or "$@" in body
    name_placeholders = any(f"${{{inp['name']}}}" in body or f"${inp['name']}" in body
                            for inp in widget.get("inputs") or [])

    if name_placeholders:
        for inp in widget.get("inputs") or []:
            name = inp["name"]
            v = (values.get(name) or "").strip() if isinstance(values.get(name), str) else str(values.get(name) or "")
            body = body.replace("${" + name + "}", v).replace("$" + name, v)
        return (
            f"[Agent View 위젯 실행: {widget.get('title') or widget.get('id')}]\n"
            f"아래 지시를 도구로 직접 수행하고, 사용자에게 보여줄 결과를 정돈된 마크다운으로 응답해라.\n"
            f"---\n{body}"
        )
    if has_placeholder:
        body = body.replace("$ARGUMENTS", args).replace("$@", args)
        return (
            f"[Agent View 위젯 실행: {widget.get('title') or widget.get('id')}]\n"
            f"아래 지시를 도구로 직접 수행하고 결과를 마크다운으로 응답해라.\n"
            f"---\n{body}"
        )
    return expand_command(cmd, args) + (
        "\n\n[참고] 이건 Agent View 위젯에서 호출됐다. 응답은 사용자에게 보여줄 결과 마크다운만 작성해라."
    )


@router.post("/api/widgets/run")
async def widget_run(req: Request):
    """Run an action widget — sends the slash command body to GPT in an isolated temporary context.
    Not persisted to DB. Emits SSE events: token/tool_start/tool_done/done/error."""
    body = await req.json()
    widget_id = (body.get("widget_id") or "").strip()
    values = body.get("inputs") or {}
    if not widget_id:
        return JSONResponse({"error": "widget_id 필수"}, status_code=400)
    if not isinstance(values, dict):
        return JSONResponse({"error": "inputs must be an object"}, status_code=400)

    widget = _find_widget(widget_id)
    if not widget:
        return JSONResponse({"error": f"widget '{widget_id}' not found"}, status_code=404)
    if widget.get("type") != "action":
        return JSONResponse({"error": f"widget '{widget_id}' is not of type 'action'"}, status_code=400)

    try:
        prompt = _render_action_prompt(widget, values)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    from pipeline.streaming import stream_gpt, build_system

    queue: asyncio.Queue = asyncio.Queue()

    async def on_token(tok: str):
        await queue.put(("token", {"token": tok}))

    async def on_tool_start(name: str, args: dict, call_id: str = ""):
        await queue.put(("tool_start", {"name": name, "call_id": call_id}))

    async def on_tool_done(name: str, result: str, call_id: str = ""):
        await queue.put(("tool_done", {"name": name, "call_id": call_id}))

    async def on_waiting():
        await queue.put(("thinking", {}))

    async def runner():
        try:
            loop = asyncio.get_event_loop()
            system_prompt = await loop.run_in_executor(None, build_system, None)
            stats: dict = {}
            await stream_gpt(
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
                on_token=on_token,
                on_tool_start=on_tool_start,
                on_tool_done=on_tool_done,
                on_waiting=on_waiting,
                working_dir=None,
                stats=stats,
            )
            await queue.put(("done", {"usage": stats}))
        except Exception as e:
            await queue.put(("error", {"message": str(e)}))
        finally:
            await queue.put((None, None))

    asyncio.create_task(runner())

    async def event_gen():
        while True:
            try:
                event, data = await asyncio.wait_for(queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if event is None:
                break
            payload = json.dumps(data, ensure_ascii=False)
            yield f"event: {event}\ndata: {payload}\n\n"
            if event in ("done", "error"):
                break

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
