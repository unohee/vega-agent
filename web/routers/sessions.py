from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from web.state import (
    _ACCESS,
    _LOAD_MODE,
    _PLAN_MODE,
    _RESEARCH_MODE,
    _SESSION_HISTORY,
    _TASK_REGISTRY,
    _YOLO_GLOBAL,
    _YOLO_MODE,
    HEARTBEAT_STALL_SEC,
    autopilot_register,
    autopilot_unregister,
    save_yolo_global,
    yolo_on,
)

router = APIRouter()

# _resume_stalled_session은 _run_gpt_task(server.py)를 직접 생성하므로
# 순환 import를 피하기 위해 지연 import 패턴을 사용한다.
def _get_resume_fn():
    from web import server as _srv
    return _srv._resume_stalled_session


_AWAITING_GRACE_SEC = 30 * 60


@router.get("/api/sessions")
async def sessions_list(include_archived: int = 0, limit: int = 30):
    from pipeline.session_store import list_sessions
    sessions = list_sessions(limit=limit, include_archived=bool(include_archived))
    return JSONResponse({"sessions": sessions})


@router.post("/api/sessions/{sid}/archive")
async def session_archive(sid: str, request: Request):
    from pipeline.session_store import set_archived
    try:
        data = await request.json()
    except Exception:
        data = {}
    archived = bool(data.get("archived", True))
    ok = set_archived(sid, archived)
    if not ok:
        return JSONResponse({"error": "세션 없음"}, status_code=404)
    return JSONResponse({"ok": True, "archived": archived})


@router.post("/api/sessions/{sid}/workdir")
async def set_session_workdir(sid: str, request: Request):
    from pipeline.session_store import set_working_dir
    data = await request.json()
    path = data.get("path")
    if path:
        p = Path(path).expanduser()
        if not p.is_dir():
            return JSONResponse({"error": f"폴더가 존재하지 않음: {path}"}, status_code=400)
        path = str(p)
    set_working_dir(sid, path)
    return JSONResponse({"ok": True, "working_dir": path})


@router.get("/api/sessions/{sid}/workdir")
async def get_session_workdir(sid: str):
    from pipeline.session_store import get_working_dir
    return JSONResponse({"working_dir": get_working_dir(sid)})


@router.get("/api/sessions/{sid}/plan-mode")
async def get_plan_mode(sid: str):
    return JSONResponse({"plan_mode": bool(_PLAN_MODE.get(sid, False))})


# ── Permission 모드 (default | plan | bypass) — chat.html 통합 토글 (INT-1452) ──
# 프론트는 이 단일 엔드포인트만 쓴다. plan→_PLAN_MODE, bypass→_YOLO_MODE 매핑으로
# 기존 plan-mode/yolo-mode 소비처(server.py)와 상태를 공유한다.

_PERM_ORDER = ["default", "plan", "bypass"]


def _permission_mode_of(sid: str) -> str:
    if _PLAN_MODE.get(sid):
        return "plan"
    if yolo_on(sid):
        return "bypass"
    return "default"


def _set_permission_mode(sid: str, mode: str) -> None:
    import web.state as _state
    if mode == "plan":
        _PLAN_MODE[sid] = True
        _YOLO_MODE.pop(sid, None)
    elif mode == "bypass":
        _YOLO_MODE[sid] = True
        _PLAN_MODE.pop(sid, None)
    else:
        _PLAN_MODE.pop(sid, None)
        _YOLO_MODE.pop(sid, None)
    # 전역 YOLO 플래그가 켜져 있으면 default/plan 으로 내려도 bypass 로 다시
    # 표시된다 — UI에서 벗어날 수 없으므로 여기서 함께 끈다.
    if mode != "bypass" and _state._YOLO_GLOBAL:
        _state._YOLO_GLOBAL = False
        save_yolo_global(False)


@router.get("/api/sessions/{sid}/permission-mode")
async def get_permission_mode(sid: str):
    return JSONResponse({"permission_mode": _permission_mode_of(sid)})


@router.post("/api/sessions/{sid}/permission-mode")
async def set_permission_mode_ep(sid: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if body.get("cycle"):
        cur = _permission_mode_of(sid)
        mode = _PERM_ORDER[(_PERM_ORDER.index(cur) + 1) % len(_PERM_ORDER)]
    else:
        mode = body.get("mode", "default")
        if mode not in _PERM_ORDER:
            return JSONResponse({"error": f"unknown mode: {mode}"}, status_code=400)
    _set_permission_mode(sid, mode)
    return JSONResponse({"permission_mode": mode})


@router.get("/api/sessions/{sid}/research-mode")
async def get_research_mode(sid: str):
    return JSONResponse({"research_mode": bool(_RESEARCH_MODE.get(sid, False))})


@router.get("/api/sessions/{sid}/yolo-mode")
async def get_yolo_mode(sid: str):
    return JSONResponse({"yolo_mode": yolo_on(sid)})


async def _toggle_mode(request: Request, sid: str, store: dict, key: str) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if "enabled" in body:
        enabled = bool(body["enabled"])
    else:
        enabled = not store.get(sid, False)
    if enabled:
        store[sid] = True
    else:
        store.pop(sid, None)
    return JSONResponse({key: enabled})


@router.post("/api/sessions/{sid}/plan-mode")
async def set_plan_mode_ep(sid: str, request: Request):
    return await _toggle_mode(request, sid, _PLAN_MODE, "plan_mode")


@router.post("/api/sessions/{sid}/research-mode")
async def set_research_mode_ep(sid: str, request: Request):
    return await _toggle_mode(request, sid, _RESEARCH_MODE, "research_mode")


@router.get("/api/sessions/{sid}/load-mode")
async def get_load_mode(sid: str):
    mode = _LOAD_MODE.get(sid, "auto")
    return JSONResponse({"load_mode": mode})


@router.post("/api/sessions/{sid}/load-mode")
async def set_load_mode_ep(sid: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = (body.get("load_mode") or body.get("mode") or "auto").strip().lower()
    if mode == "auto":
        _LOAD_MODE.pop(sid, None)
    elif mode in ("light", "standard", "heavy", "fast"):
        _LOAD_MODE[sid] = "light" if mode == "fast" else mode
    else:
        return JSONResponse({"error": f"unknown load_mode: {mode}"}, status_code=400)
    return JSONResponse({"load_mode": _LOAD_MODE.get(sid, "auto")})


@router.post("/api/sessions/{sid}/yolo-mode")
async def set_yolo_mode_ep(sid: str, request: Request):
    global _YOLO_GLOBAL
    import web.state as _state
    try:
        body = await request.json()
    except Exception:
        body = {}
    if "enabled" in body:
        _state._YOLO_GLOBAL = bool(body["enabled"])
    else:
        _state._YOLO_GLOBAL = not _state._YOLO_GLOBAL
    if not _state._YOLO_GLOBAL:
        _YOLO_MODE.clear()
    save_yolo_global(_state._YOLO_GLOBAL)
    return JSONResponse({"yolo_mode": _state._YOLO_GLOBAL})


@router.post("/api/sessions/{sid}/resume")
async def resume_session_ep(sid: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if body.get("autopilot"):
        autopilot_register(sid)
    reg = _TASK_REGISTRY.get(sid)
    if reg and not reg.get("done"):
        idle = time.monotonic() - reg.get("last_activity", time.monotonic())
        if idle < HEARTBEAT_STALL_SEC:
            return JSONResponse({"resumed": False, "reason": f"already running (idle {int(idle)}s)",
                                 "autopilot": bool(body.get("autopilot"))})
    try:
        _get_resume_fn()(sid)
        from web.state import _heartbeat_resumes
        return JSONResponse({"resumed": True, "resume_count": _heartbeat_resumes.get(sid, 0),
                             "autopilot": bool(body.get("autopilot"))})
    except Exception as e:
        return JSONResponse({"resumed": False, "reason": str(e)}, status_code=500)


@router.post("/api/sessions/{sid}/autopilot-off")
async def autopilot_off_ep(sid: str):
    autopilot_unregister(sid)
    return JSONResponse({"autopilot": False})


@router.get("/api/sessions/active")
async def get_active_sessions():
    now = time.time()
    zombies: list[str] = []
    active: list[dict] = []
    for sid, reg in _TASK_REGISTRY.items():
        if reg.get("done"):
            continue
        awaiting = bool(reg.get("awaiting_approval", False))
        awaiting_since = reg.get("awaiting_since")
        age = int(now - awaiting_since) if awaiting_since else None
        if awaiting and age is not None and age > _AWAITING_GRACE_SEC:
            zombies.append(sid)
            continue
        active.append({
            "sid": sid,
            "awaiting_approval": awaiting,
            "awaiting_age_sec": age,
            "last_event_id": len(reg.get("buf", [])),
        })
    for zsid in zombies:
        reg = _TASK_REGISTRY.get(zsid)
        if not reg:
            continue
        aq = reg.get("approval_queue")
        if aq is not None and aq.empty():
            try:
                aq.put_nowait({"approved": False, "result": None, "__aborted__": True})
            except Exception:
                pass
        task = reg.get("task")
        if task and not task.done():
            task.cancel()
    return JSONResponse({"active": active})


@router.get("/api/sessions/{sid}/history")
async def session_history(sid: str):
    from pipeline.session_store import get_session, load_history_with_meta
    session = get_session(sid)
    if not session:
        return JSONResponse({"error": "not found"}, status_code=404)
    messages = load_history_with_meta(sid)
    return JSONResponse({"uuid": sid, "name": session["name"], "messages": messages})


@router.post("/api/sessions")
async def session_create(request: Request):
    from pipeline.session_store import create_session
    data = await request.json()
    title = data.get("title", "VEGA 세션")
    sid = create_session(title)
    _SESSION_HISTORY[sid] = []
    return JSONResponse({"uuid": sid, "name": title})


@router.put("/api/sessions/{sid}/rename")
async def session_rename(sid: str, request: Request):
    from pipeline.session_store import rename_session
    data = await request.json()
    name = data.get("name", "VEGA 세션")
    rename_session(sid, name)
    return JSONResponse({"ok": True})


@router.delete("/api/sessions/{sid}")
async def session_delete(sid: str):
    from pipeline.session_store import delete_session
    delete_session(sid)
    _SESSION_HISTORY.pop(sid, None)
    _PLAN_MODE.pop(sid, None)
    _ACCESS.pop(sid, None)
    return JSONResponse({"ok": True})
