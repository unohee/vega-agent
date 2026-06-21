# Created: 2026-06-21
# Purpose: Live sub-agent spawn registry and dispatch_agent tool support.
# Dependencies: asyncio, pipeline.streaming

from __future__ import annotations

import asyncio
import time
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


_thread_ctx = threading.local()
_lock = threading.RLock()
_TREES: dict[str, dict[str, dict[str, Any]]] = {}
_DONE_RETENTION_SEC = 600.0
_MAX_EVENTS = 200
_MAX_TEXT = 6000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_dispatch_context(**ctx: Any) -> None:
    """현재 tool dispatch 스레드에 부모 세션/이벤트 루프 컨텍스트를 주입한다."""
    _thread_ctx.current = ctx


def clear_dispatch_context() -> None:
    if hasattr(_thread_ctx, "current"):
        delattr(_thread_ctx, "current")


def _current_context() -> dict[str, Any] | None:
    ctx = getattr(_thread_ctx, "current", None)
    return ctx if isinstance(ctx, dict) else None


def _snapshot_agent(agent: dict[str, Any]) -> dict[str, Any]:
    snap = {
        k: v for k, v in agent.items()
        if k not in {"task", "parent_reg", "loop"}
    }
    snap["events"] = list(agent.get("events", []))
    return snap


def _prune_locked(parent_session_id: str) -> None:
    now = time.monotonic()
    tree = _TREES.get(parent_session_id)
    if not tree:
        return
    for agent_id, agent in list(tree.items()):
        done_at = agent.get("_done_monotonic")
        if done_at is not None and now - done_at > _DONE_RETENTION_SEC:
            tree.pop(agent_id, None)
    if not tree:
        _TREES.pop(parent_session_id, None)


def _append_agent_event(agent: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
    event = {"ts": _utc_now(), "type": event_type, **payload}
    events = agent.setdefault("events", [])
    events.append(event)
    if len(events) > _MAX_EVENTS:
        del events[: len(events) - _MAX_EVENTS]
    agent["updated_at"] = event["ts"]


def _emit_parent(reg: dict[str, Any], payload: dict[str, Any]) -> None:
    reg["buf"].append({"event": "spawn_update", "data": payload})
    reg["last_activity"] = time.monotonic()
    reg["consumer"].set()


def _emit_update(agent: dict[str, Any], event_type: str, payload: dict[str, Any] | None = None) -> None:
    payload = payload or {}
    parent_session_id = agent["parent_session_id"]
    with _lock:
        _append_agent_event(agent, event_type, payload)
        snap = _snapshot_agent(agent)
    reg = agent.get("parent_reg")
    if not reg:
        return
    _emit_parent(reg, {
        "type": event_type,
        "session_id": parent_session_id,
        "agent": snap,
    })


async def _run_child_agent(agent: dict[str, Any]) -> None:
    parent_session_id = agent["parent_session_id"]
    agent_id = agent["id"]
    prompt = agent["prompt"]
    working_dir = agent.get("working_dir")
    loop = asyncio.get_running_loop()
    partial: list[str] = []

    async def on_waiting() -> None:
        _emit_update(agent, "thinking", {"label": "생각 중"})

    async def on_token(tok: str) -> None:
        partial.append(tok)
        with _lock:
            text = (agent.get("text") or "") + tok
            agent["text"] = text[-_MAX_TEXT:]
            agent["token_count"] = agent.get("token_count", 0) + 1
        _emit_update(agent, "token", {"token": tok})

    async def on_tool_start(name: str, args: dict, call_id: str = "") -> None:
        with _lock:
            agent["active_tool"] = name
            agent["tool_count"] = agent.get("tool_count", 0) + 1
        _emit_update(agent, "tool_start", {
            "name": name,
            "call_id": call_id,
            "args": _clip_args(args),
        })

    async def on_tool_done(name: str, result: str, call_id: str = "") -> None:
        summary = "완료"
        try:
            if isinstance(result, str) and result.lstrip().startswith("{"):
                import json
                parsed = json.loads(result)
                if isinstance(parsed, dict) and parsed.get("error"):
                    summary = f"오류: {str(parsed['error'])[:160]}"
        except Exception:
            pass
        with _lock:
            agent.pop("active_tool", None)
        _emit_update(agent, "tool_done", {"name": name, "call_id": call_id, "summary": summary})

    try:
        with _lock:
            agent["status"] = "running"
            agent["started_at"] = _utc_now()
            agent["updated_at"] = agent["started_at"]
        _emit_update(agent, "started", {"label": agent.get("label") or "Sub-agent"})

        from pipeline.streaming import build_system, stream_gpt

        system = await loop.run_in_executor(None, build_system, working_dir)
        system = (
            "You are a VEGA sub-agent spawned by the parent turn. "
            "Work independently, keep output concise, and return findings for the parent.\n\n"
            + system
        )
        child_spawn_context = {
            "parent_session_id": parent_session_id,
            "parent_agent_id": agent_id,
            "parent_reg": agent.get("parent_reg"),
            "loop": loop,
            "working_dir": working_dir,
            "plan_mode": bool(agent.get("plan_mode")),
            "ce_mode": bool(agent.get("ce_mode")),
            "research_mode": bool(agent.get("research_mode")),
        }
        result = await stream_gpt(
            [{"role": "user", "content": prompt}],
            system=system,
            on_token=on_token,
            on_tool_start=on_tool_start,
            on_tool_done=on_tool_done,
            on_waiting=on_waiting,
            working_dir=working_dir,
            plan_mode=bool(agent.get("plan_mode")),
            ce_mode=bool(agent.get("ce_mode")),
            research_mode=bool(agent.get("research_mode")),
            spawn_context=child_spawn_context,
        )
        with _lock:
            agent["status"] = "done"
            agent["result"] = result[-_MAX_TEXT:]
            agent["_done_monotonic"] = time.monotonic()
            agent.pop("active_tool", None)
        _emit_update(agent, "done", {"result": result[-1200:]})
    except asyncio.CancelledError:
        with _lock:
            agent["status"] = "cancelled"
            agent["result"] = "".join(partial)[-_MAX_TEXT:]
            agent["_done_monotonic"] = time.monotonic()
            agent.pop("active_tool", None)
        _emit_update(agent, "cancelled", {})
    except Exception as exc:
        with _lock:
            agent["status"] = "error"
            agent["error"] = str(exc)
            agent["result"] = "".join(partial)[-_MAX_TEXT:]
            agent["_done_monotonic"] = time.monotonic()
            agent.pop("active_tool", None)
        _emit_update(agent, "error", {"error": str(exc)})


def _clip_args(args: dict[str, Any]) -> dict[str, Any]:
    clipped: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if isinstance(value, str) and len(value) > 240:
            clipped[key] = value[:240] + "..."
        else:
            clipped[key] = value
    return clipped


async def _start_agent(ctx: dict[str, Any], prompt: str, label: str) -> str:
    parent_session_id = str(ctx["parent_session_id"])
    parent_agent_id = ctx.get("parent_agent_id")
    agent_id = uuid.uuid4().hex[:12]
    now = _utc_now()
    agent: dict[str, Any] = {
        "id": agent_id,
        "parent_session_id": parent_session_id,
        "parent_agent_id": parent_agent_id,
        "label": label or "Sub-agent",
        "prompt": prompt,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "events": [],
        "text": "",
        "token_count": 0,
        "tool_count": 0,
        "working_dir": ctx.get("working_dir"),
        "plan_mode": bool(ctx.get("plan_mode")),
        "ce_mode": bool(ctx.get("ce_mode")),
        "research_mode": bool(ctx.get("research_mode")),
        "parent_reg": ctx.get("parent_reg"),
    }
    with _lock:
        _prune_locked(parent_session_id)
        _TREES.setdefault(parent_session_id, {})[agent_id] = agent
    task = asyncio.create_task(_run_child_agent(agent))
    with _lock:
        agent["task"] = task
    return agent_id


async def _wait_for_agent(parent_session_id: str, agent_id: str) -> dict[str, Any]:
    while True:
        with _lock:
            agent = _TREES.get(parent_session_id, {}).get(agent_id)
            if not agent:
                return {"id": agent_id, "status": "missing"}
            snap = _snapshot_agent(agent)
            if snap.get("status") in {"done", "error", "cancelled"}:
                return snap
        await asyncio.sleep(0.05)


def dispatch_agent(
    prompt: str,
    label: str = "",
    wait_for_result: bool = False,
    timeout_sec: int = 900,
) -> dict[str, Any]:
    """LLM tool entrypoint: spawn an independent child agent for the current parent turn."""
    prompt = (prompt or "").strip()
    label = (label or "").strip()[:80]
    if not prompt:
        return {"error": "prompt is required"}
    ctx = _current_context()
    if not ctx or not ctx.get("loop") or not ctx.get("parent_reg") or not ctx.get("parent_session_id"):
        return {"error": "dispatch_agent is only available during a live VEGA turn"}
    loop = ctx["loop"]
    start_future = asyncio.run_coroutine_threadsafe(_start_agent(ctx, prompt, label), loop)
    try:
        agent_id = start_future.result(timeout=2)
    except Exception as exc:
        return {"error": f"failed to start sub-agent: {exc}"}

    out: dict[str, Any] = {
        "ok": True,
        "agent_id": agent_id,
        "status": "running",
        "message": "Sub-agent spawned. Watch the Agents chip for live progress.",
    }
    if wait_for_result:
        wait_future = asyncio.run_coroutine_threadsafe(
            _wait_for_agent(str(ctx["parent_session_id"]), agent_id),
            loop,
        )
        try:
            out["agent"] = wait_future.result(timeout=max(1, int(timeout_sec)))
            out["status"] = out["agent"].get("status", "done")
        except Exception as exc:
            out["status"] = "timeout"
            out["timeout"] = str(exc)
    return out


def list_tree(parent_session_id: str | None = None, include_done: bool = True) -> list[dict[str, Any]]:
    with _lock:
        if parent_session_id:
            _prune_locked(parent_session_id)
            trees = {parent_session_id: _TREES.get(parent_session_id, {})}
        else:
            for sid in list(_TREES):
                _prune_locked(sid)
            trees = _TREES
        rows: list[dict[str, Any]] = []
        for tree in trees.values():
            for agent in tree.values():
                if not include_done and agent.get("status") in {"done", "error", "cancelled"}:
                    continue
                rows.append(_snapshot_agent(agent))
    rows.sort(key=lambda r: r.get("created_at", ""))
    return deepcopy(rows)


def active_count(parent_session_id: str | None = None) -> int:
    return sum(1 for a in list_tree(parent_session_id, include_done=True)
               if a.get("status") not in {"done", "error", "cancelled"})
