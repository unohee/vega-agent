# Created: 2026-05-18
# Purpose: VEGA FastAPI server — replaces Chainlit
# Dependencies: fastapi, uvicorn, pipeline/streaming.py, pipeline/session_store.py
# Test Status: under validation

from __future__ import annotations

import asyncio
import logging
import json
import os
import struct
import sys
import time
from pathlib import Path

# Windows 콘솔 기본 인코딩(cp949/cp1252)에선 한국어·em-dash(—)·✓ 등을 print 하면
# UnicodeEncodeError 가 난다. PyInstaller exe 는 launcher 가 stdout/stderr 를 UTF-8 로
# reconfigure 하지만, uvicorn 직접 기동·pytest·기타 진입점은 그 보호를 안 받는다.
# 모든 기동 경로의 공통 관문인 server import 시점에 한 번 더 고정한다(idempotent). (INT-1506 후속)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # 파이프/리다이렉트 등 reconfigure 미지원 스트림은 무시

# PTY 터미널(/ws/terminal)용 unix 전용 모듈 — Windows 에는 없다.
# import 실패 시 내장 터미널 기능만 비활성하고 서버는 정상 기동한다 (INT-1438).
try:
    import fcntl
    import pty
    import termios
    _HAS_PTY = True
except ImportError:  # Windows
    fcntl = pty = termios = None  # type: ignore[assignment]
    _HAS_PTY = False

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.session_store import (
    append_message,
    create_session,
    delete_session,
    get_or_create_session,
    get_session,
    get_working_dir,
    list_sessions,
    load_history,
    rename_session,
    set_working_dir,
)

_DEFAULT_SESSION_NAMES = {"VEGA 세션", "테스트 세션", "New Chat", ""}


async def _auto_title_session(sid: str) -> None:
    """
    Called in the background immediately after the first round-trip (msg_count==2).
    Generates a title via _lms_title_session → rename_session + session_digest save.
    Exits silently if a title already exists or the LLM call fails.
    Pushes a session_titled event to the SSE registry if still connected.
    """
    try:
        session = get_session(sid)
        if not session:
            return
        if session.get("name", "") not in _DEFAULT_SESSION_NAMES:
            return  # title already set

        messages = load_history(sid)
        if not messages:
            return

        loop = asyncio.get_event_loop()
        from pipeline.heartbeat import _lms_title_session, _save_session_digest
        result = await loop.run_in_executor(None, _lms_title_session, messages)
        if not result:
            return

        title = result.get("title", "").strip()
        summary = result.get("summary", "").strip()
        narrative = result.get("narrative", "").strip()

        if title:
            rename_session(sid, title)
            _save_session_digest(sid, title, summary, narrative)
            # push title-change event if SSE stream is still connected
            reg = _TASK_REGISTRY.get(sid)
            if reg:
                _push_event(reg, {"event": "session_titled", "data": {"session_id": sid, "title": title}})
    except Exception as e:
        print(f"[auto_title] failed (ignored): {e}")
from pipeline.vega_query import (
    context_for_date,
    events_by_date,
    events_by_entity,
    events_by_tag,
    get_entity,
    get_persona,
    search_events,
)
from contextlib import asynccontextmanager

from pipeline.streaming import build_system, stream_gpt
from pipeline.mcp_client import init_mcp_tools
from pipeline.tools import TOOL_SCHEMAS
from pipeline.contact_store import startup_sync
from pipeline.compaction import _keep_recent, compact_history, _needs_compaction, splice_compacted

STATIC_DIR = Path(__file__).parent / "static"

from pipeline.data_paths import charts_dir as _charts_dir, uploads_dir as _uploads_dir
CHART_DIR = _charts_dir()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sync contacts DB — 백그라운드 (기동을 3-10초 블로킹하던 것 제거, INT-1430).
    # 동기화 완료 전 연락처 도구는 직전 동기화 시점의 DB를 본다 — 허용 가능한 staleness.
    async def _sync_contacts():
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, startup_sync)
            print(f"[Contacts] iCloud sync done: {result['synced']} updated, {result['total']} total")  # cxt-ignore: fake_execution
        except Exception as e:
            print(f"[Contacts] sync warning: {e}")

    asyncio.create_task(_sync_contacts())

    # Collect MCP tool list → merge into TOOL_SCHEMAS — 백그라운드 (INT-1430).
    # 서버별 연결(+실패 재시도)이 기동을 수 초씩 블로킹하던 것 제거. 연결 완료 전
    # 첫 턴은 MCP 도구 없이 진행될 수 있다 — 앱이 8초 늦게 뜨는 것보다 낫다.
    async def _init_mcp():
        try:
            mcp_schemas = await init_mcp_tools()
            for server_name, schemas in mcp_schemas.items():
                TOOL_SCHEMAS.extend(schemas)
                print(f"[MCP] {server_name}: {len(schemas)} tools registered")
        except Exception as e:
            print(f"[MCP] init warning: {e}")

    asyncio.create_task(_init_mcp())

    # Background warmup if active LLM provider is local (LM Studio/Ollama etc.) —
    # avoids 70-second prefill penalty on first real request
    async def _warmup_local_llm():
        try:
            from pipeline.llm_gateway import get_active_provider
            prov = get_active_provider()
            if prov.get("auth_type") != "none":
                return  # local providers only
            base_url = prov.get("base_url", "")
            model = prov.get("default_model", "")
            if not base_url.startswith("http://localhost") and not base_url.startswith("http://127."):
                return
            import urllib.request as _ur
            url = base_url.rstrip("/") + "/chat/completions"
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "."}],
                "stream": False, "max_tokens": 1,
            }
            req = _ur.Request(url, data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: _ur.urlopen(req, timeout=360).read()
            )
            print(f"[LLM warmup] {prov['name']} ({model}) ready")
        except Exception as e:
            print(f"[LLM warmup] skipped: {e}")

    asyncio.create_task(_warmup_local_llm())

    # YOLO heartbeat — find stalled YOLO sessions in the background and auto-resume
    _hb_task = asyncio.create_task(_yolo_heartbeat())

    # Cron loop — run scheduled prompts at their due time in the background (INT-1407)
    _cron_task = asyncio.create_task(_cron_loop())

    # WhatsApp GoWA sidecar — opt-in (VEGA_WHATSAPP_SIDECAR). Off by default;
    # when enabled, manage lifecycle in the background so app startup is not held.
    async def _start_whatsapp_sidecar():
        try:
            def _start():
                from pipeline import whatsapp_sidecar
                return whatsapp_sidecar, whatsapp_sidecar.start()
            whatsapp_sidecar, _wa = await asyncio.get_event_loop().run_in_executor(None, _start)
            if _wa.get("started"):
                print(f"[WhatsApp] GoWA sidecar started (pid={_wa.get('pid')}, port={_wa.get('port')})")  # cxt-ignore: fake_execution
            elif whatsapp_sidecar.is_enabled():
                print(f"[WhatsApp] GoWA sidecar not started: {_wa.get('reason')}")
        except Exception as e:
            print(f"[WhatsApp] sidecar init warning: {e}")

    asyncio.create_task(_start_whatsapp_sidecar())

    # 코드 실행은 호스트 동봉 인터프리터로 직접 동작(Docker 제거, INT-1870) — 샌드박스 warmup 불필요.

    # heartbeat은 이 repo(agent.db 분기)에서 테이블 사전생성 함수 없음 — 생략

    async def _init_project_state():
        try:
            def _init():
                from pipeline.project_state import _ensure_project_state_table, seed_project_states
                _ensure_project_state_table()
                seed_project_states()
            await asyncio.get_event_loop().run_in_executor(None, _init)
        except Exception as e:
            print(f"[ProjectState] table init warning: {e}")

    asyncio.create_task(_init_project_state())

    yield

    # Cancel heartbeat·cron tasks on shutdown
    _hb_task.cancel()
    _cron_task.cancel()

    # Stop the WhatsApp GoWA sidecar if VEGA started it (no-op otherwise)
    try:
        from pipeline import whatsapp_sidecar
        whatsapp_sidecar.stop()
    except Exception:
        pass


app = FastAPI(title="VEGA", lifespan=lifespan)

# Register routers (web/routers/*)
from web.routers import llm as _llm_router  # noqa: E402
from web.routers import fs as _fs_router  # noqa: E402
from web.routers import dashboard as _dashboard_router  # noqa: E402
from web.routers import widgets as _widgets_router  # noqa: E402
from web.routers import onboarding as _onboarding_router  # noqa: E402
from web.routers import run_log as _run_log_router  # noqa: E402
from web.routers import scheduler as _scheduler_router  # noqa: E402
from web.routers import memory_inspector as _memory_inspector_router  # noqa: E402
from web.routers import data_boundary as _data_boundary_router  # noqa: E402
from web.routers import cron as _cron_router  # noqa: E402
from web.routers import oauth as _oauth_router  # noqa: E402
from web.routers import upload as _upload_router  # noqa: E402
from web.routers import stt as _stt_router  # noqa: E402
from web.routers import sessions as _sessions_router  # noqa: E402
from web.routers import admin as _admin_router  # noqa: E402
from web.routers import spawn as _spawn_router  # noqa: E402
from web.routers import network as _network_router  # noqa: E402
app.include_router(_llm_router.router)
app.include_router(_fs_router.router)
app.include_router(_dashboard_router.router)
app.include_router(_widgets_router.router)
app.include_router(_onboarding_router.router)
app.include_router(_run_log_router.router)
app.include_router(_scheduler_router.router)
app.include_router(_memory_inspector_router.router)
app.include_router(_data_boundary_router.router)  # local-first data boundary export/wipe (INT-1383)
app.include_router(_cron_router.router)  # arbitrary-prompt cron jobs CRUD (INT-1407)
app.include_router(_oauth_router.router)
app.include_router(_upload_router.router)
app.include_router(_stt_router.router)
app.include_router(_sessions_router.router)
app.include_router(_admin_router.router)
app.include_router(_spawn_router.router)
app.include_router(_network_router.router)

# CORS — allow Tauri app origin + localhost only. Wildcard removed to block cross-origin CSRF.
from fastapi.middleware.cors import CORSMiddleware
# 허용 Origin — Tauri 앱 + localhost 전용. CORS 미들웨어와 WebSocket Origin 검증이 공유.
_ALLOWED_ORIGINS = [
    "tauri://localhost",  # cxt-ignore: fake_data
    "http://localhost:8100",  # cxt-ignore: fake_data
    "http://127.0.0.1:8100",  # cxt-ignore: fake_data
    "http://localhost:8101",  # cxt-ignore: fake_data
    "http://127.0.0.1:8101",  # cxt-ignore: fake_data
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 원격 침입 차단 미들웨어 (INT-1468 H2) ────────────────────────────────────
# 백엔드는 127.0.0.1 전용 바인드라 정상 상태에선 원격 peer 가 닿지 않지만,
# 사용자가 터널/프록시(예: Tailscale)로 노출할 때를 대비한 명시 게이트.
# 허용: loopback · Tailscale 대역 · VEGA_REMOTE_ALLOW_CIDRS · 유효 enterprise 키.
# 그 외 원격은 403. /api/health 만 예외(가용성 프로브).
_REMOTE_GATE_EXEMPT = {"/api/health"}


@app.middleware("http")
async def _remote_access_gate(request: Request, call_next):
    path = request.url.path
    if path not in _REMOTE_GATE_EXEMPT and not _state_mod.is_remote_allowed(request):
        from fastapi.responses import JSONResponse as _JR
        return _JR(
            {"error": "원격 접속이 허용되지 않았습니다. 로컬 앱에서 사용하거나, "
                      "Tailscale/신뢰 네트워크 또는 enterprise 키로 접속하세요."},
            status_code=403,
        )
    return await call_next(request)


app.mount("/api/charts", StaticFiles(directory=str(CHART_DIR)), name="charts")
# /static — chat.html 등이 번들 자산(vendor/pdfjs 등)을 로드 (INT-1831)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 공유 런타임 상태 — web/state.py에서 관리. 모두 같은 객체를 참조함.
from web.state import (  # noqa: E402
    _SESSION_HISTORY,
    _PLAN_MODE,
    _RESEARCH_MODE,
    _LOAD_MODE,
    _YOLO_MODE,
    _GOAL_MODE,
    _TASK_REGISTRY,
    _ACCESS,
    _ENT_KEY_KC,
    _heartbeat_resumes,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_STALL_SEC,
    HEARTBEAT_MAX_RESUMES,
    HEARTBEAT_DB_MAX_RESUMES,
    WATCHDOG_IDLE_DEFAULT,
    WATCHDOG_IDLE_LONG,
    autopilot_register as _autopilot_register,
    autopilot_unregister as _autopilot_unregister,
    save_yolo_global as _save_yolo_global,
    load_enterprise_keys as _load_enterprise_keys,
    watchdog_idle_for as _watchdog_idle_for,
    yolo_on as _yolo_on,
)
import web.state as _state_mod

# 하위호환 re-export — 기존 테스트가 web.server.* 로 참조
from web.state import (  # noqa: E402
    _yolo_flag_path,
    _load_yolo_global,
    save_yolo_global as _save_yolo_global,  # type: ignore[assignment]
    _autopilot_path,
    _load_autopilot,
    _save_autopilot,
)


def _yolo_on(sid: str) -> bool:
    return _state_mod.yolo_on(sid)


def _watchdog_idle_for(sid: str) -> float:
    return _state_mod.watchdog_idle_for(sid)


def _is_stalled_yolo(sid: str, reg: dict, now_monotonic: float) -> bool:
    """Is this a heartbeat resume target — YOLO + not done + idle over threshold + not awaiting + under cap."""
    if not _yolo_on(sid):
        return False
    if reg.get("done"):
        return False
    if reg.get("awaiting_approval"):
        return False
    if _heartbeat_resumes.get(sid, 0) >= HEARTBEAT_MAX_RESUMES:
        return False
    idle = now_monotonic - reg.get("last_activity", now_monotonic)
    return idle > HEARTBEAT_STALL_SEC


def _load_autopilot() -> dict:
    from web.state import _load_autopilot as _la
    return _la()


def _save_autopilot(data: dict) -> None:
    from web.state import _save_autopilot as _sa
    _sa(data)


_RESEARCH_MODE_GUIDE = """## 연구 모드 (Research Mode) 활성화

지금 이 요청은 **연구 모드**로 처리된다. 다음 원칙을 반드시 따른다:

1. **가설 → 근거 → 결론** 3단계로 추론한다.
   - 먼저 "이 질문에 답하려면 무엇을 알아야 하는가"를 내부적으로 열거한다.
   - 각 핵심 사항을 `web_search` 또는 도구 호출로 실제 확인한다.
   - 수집한 근거를 종합해 결론을 도출한다.

2. **검색을 반드시 사용한다.** 기억에만 의존하지 말고 `web_search`로 최신 정보를 수집한다.
   특히 수치·날짜·버전·현재 상태는 검색 없이 단언하지 않는다.

3. **출처를 명시한다.** 사실 주장마다 근거(URL, 파일명, 검색 결과)를 인라인으로 표시한다.

4. **불확실성을 표시한다.** 확신도가 낮은 주장은 "추정:", "불확실:" 등으로 명확히 구분한다.

5. **응답 형식**: TL;DR(2~3줄) → 상세 분석 → 출처 목록 순으로 작성한다.
"""

# Access level — per session.
# "local"      : loopback connection (Tauri app, local terminal)
# "enterprise" : remote client with valid X-VEGA-Key header
# 원격 차단은 _remote_access_gate 미들웨어 + state.is_remote_allowed 가 담당.
# _ACCESS 는 web.state 에서 import 한 공유 dict 를 그대로 쓴다 — 여기서 재할당하면
# web.routers.sessions 의 access 정리가 다른 객체를 봐 분열된다 (INT-2234).

_ENT_KEY_KC = "vega-enterprise-keys"   # Keychain service name


def _load_enterprise_keys() -> frozenset[str]:
    """Load enterprise key list from Keychain. Comma-separated string."""
    try:
        from pipeline.keychain import get_secret
        raw = get_secret(_ENT_KEY_KC, service=_ENT_KEY_KC) or ""
        return frozenset(k.strip() for k in raw.split(",") if k.strip())
    except Exception:
        return frozenset()


# _is_loopback / _get_access_level 은 web/state.py 단일 출처로 위임한다
# (INT-1468 H1: XFF 신뢰 로직 중복 제거 + 신뢰 프록시 opt-in 일원화).
def _is_loopback(request: Request) -> bool:
    return _state_mod.is_loopback(request)


def _get_access_level(request: Request) -> str:
    """요청의 접근 레벨: 'local' | 'enterprise'.

    원격 차단은 _remote_access_gate 미들웨어가 담당(허용된 원격만 여기 도달).
    여기서는 loopback→local, 유효 키→enterprise, 그 외(허용된 Tailscale 등)→local.
    """
    if _state_mod.is_loopback(request):
        return "local"
    key = request.headers.get("x-vega-key", "").strip()
    if key and key in _load_enterprise_keys():
        return "enterprise"
    return "local"


# 하위 호환 — ce_mode bool이 필요한 곳에서 사용. CE 모드 폐지로 항상 False.
def _ce_mode_from_access(level: str) -> bool:
    return False


_PLAN_MODE_GUIDE = """## 🔒 Plan 모드 활성

지금 너는 plan 모드다. 다음 규칙을 반드시 지켜라:

1. **실행 도구 차단**: bash_exec, python_exec, host_exec, file_edit, gmail_send,
   calendar_create_event/update/delete, things_add/update/complete, skill_save/delete,
   widget_save/delete, mcp_add_server/remove/reload, linear_create_issue/update/add_comment,
   memory_*_update/add, gmail_modify_labels/batch_modify, icloud_move/rename/mkdir,
   contact_memo_update 등 **쓰기·실행·외부 전송 계열** 도구는 호출하면 안 된다.
   (호출해도 서버가 차단하고 거절 응답을 돌려준다.)

2. **읽기 도구는 허용**: web_search, web_fetch, gmail_search, gmail_read, calendar_list_events,
   drive_search, drive_read, file_read, imessage_search, contacts_*, linear_list/get/search,
   xlsx_/docx_/pptx_ 읽기, vega_query, persona/event/entity 조회 등은 사용해라
   (정보 수집은 계획 수립에 필수).

3. **출력 형식**: 코드는 마크다운 코드 블록으로만 보여라. 실행하지 마라.
   체크리스트·단계별 계획·검증 기준·영향 범위를 명확하게 제시해라.

4. **종료 방법**: 계획이 충분히 다듬어졌다고 판단되면 `exit_plan_mode(plan="...")` 도구를 호출해
   사용자에게 승인을 요청해라. 사용자가 승인하면 자동으로 plan 모드가 해제되고 실행 단계로 넘어간다.

5. **plan 모드 유지 동안**: 사용자가 추가로 요청을 던지면 그것도 plan으로 흡수하고 계획을 갱신해라.
   사용자가 `/plan-off`를 명시적으로 입력하면 즉시 plan 모드가 해제된다.
"""

# /goal long-running protocol — injected into system once (not resent as a user
# message every turn). Compressed from goal.md to cut token accumulation + self-echo.
_GOAL_MODE_GUIDE = """## 장기작업 모드 (Goal Mode) 활성

이 세션은 멀티턴 장기작업이다. 끝까지 이어가되 **간결하게** 진행한다.

1. 도구 호출 직전: 무엇을 왜 하는지 **한 줄로만** 말한다. 장황한 상황 재설명 금지.
2. 이미 한 작업·이미 말한 맥락을 매 턴 반복하지 마라. 새로 바뀐 것만 말한다.
3. 도구 결과는 1~3줄로 해석하고 바로 다음 행동으로 넘어간다.
4. 턴을 끝낼 때만 짧은 체크포인트(완료/남은 것/다음)를 남긴다. 매 도구마다 카드 재작성 금지.
5. 완료조건: 산출물 생성·검증·이어받기 가능 상태·최종 요약. 충족 전 완료 선언 금지.

핵심: **직전에 한 말을 되풀이하지 않는다.** 진척만 보고한다."""

# ── Task registry — tasks run to completion regardless of connection state ─────────────────────
# key: session_id
# value: {
#   "task": asyncio.Task,          # run_gpt task
#   "buf":  list[dict],            # unconsumed event buffer
#   "done": bool,                  # task fully finished
#   "consumer": asyncio.Event,     # signals new event arrival
# }
# _TASK_REGISTRY 는 web.state 에서 import 한 공유 dict — 여기서 재할당하면 web.routers.sessions
# 의 active/resume/zombie cleanup 이 빈 registry 를 보게 된다 (INT-2234, CRITICAL).


def _get_history(sid: str) -> list[dict]:
    if sid not in _SESSION_HISTORY:
        _SESSION_HISTORY[sid] = load_history(sid)
    return _SESSION_HISTORY[sid]


# ── Tool status labels ────────────────────────────────────────────────────────

def _core_command(command: str) -> str:
    """Extract the actual core program name from a shell command.
    'cd X && find . -name ...' → 'find', 'FOO=1 python3 - <<PY' → 'python3'.
    Skips prep tokens (cd/export/pure env assignments) and returns the first real
    command's basename. Pipelines use the first real command. Full command stays in
    the UI accordion (args)."""
    import re as _re
    if not command:
        return ""
    segments = _re.split(r"\s*(?:&&|\|\||;|\|)\s*", command.strip())
    seg_skip = {"cd", "export", "set", "source", "."}          # whole segment is prep → next segment
    wrap_skip = {"sudo", "env", "time", "nohup", "exec", "command", "xargs", "nice"}  # wrapper → next token
    for seg in segments:
        toks = seg.strip().split()
        i = 0
        while i < len(toks):
            if _re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[i]):  # env assignment prefix
                i += 1
                continue
            base = toks[i].rsplit("/", 1)[-1]
            if base in wrap_skip:
                i += 1
                while i < len(toks) and toks[i].startswith("-"):
                    i += 1
                    if i < len(toks) and toks[i].isdigit():
                        i += 1
                continue
            if base in seg_skip:
                break
            return base
    first = segments[0].strip().split()
    return (first[0].rsplit("/", 1)[-1] if first else "")


def _tool_label(name: str, args: dict) -> str:
    query = args.get("query", "")
    core = _core_command(args.get("command", ""))
    return {
        "web_search":            f"🔍 검색 중: {query}",
        "web_fetch":             f"🌐 페이지 읽는 중: {args.get('url', '')[:60]}",
        "gmail_search":          f"📧 메일 검색 중: {query}",
        "gmail_read":            f"📧 메일 읽는 중…",
        "gmail_send":            f"📤 메일 전송 중 → {args.get('to', '')}",
        "calendar_list_events":  f"📅 일정 확인 중…",
        "calendar_create_event": f"📅 일정 추가 중: {args.get('summary', '')}",
        "drive_search":          f"📂 Drive 검색 중: {query}",
        "drive_read":            f"📂 Drive 파일 읽는 중…",
        "host_exec":             f"🖥️  실행 중: `{core}`" if core else "🖥️  호스트 실행 중…",
        "bash_exec":             f"⚙️  실행 중: `{core}`" if core else "⚙️  실행 중…",
        "python_exec":           f"🐍 Python 실행 중…",
        "chart_matplotlib":      f"📊 차트 그리는 중…",
        "chart_plotly":          f"📊 인터랙티브 차트 그리는 중…",
        "system_info":           f"💻 시스템 상태 확인 중…",
        "memory_persona_update": f"🧠 페르소나 [{args.get('section_key', '')}] 업데이트 중…",
        "memory_event_add":      f"📝 이벤트 기록 중: {args.get('title', '')}",
        "memory_entity_upsert":  f"🗂️  엔티티 저장 중: {args.get('name', '')}",
        "file_read":             f"📄 파일 읽는 중: {args.get('path', '')[:50]}",
        "file_edit":             f"✏️  파일 편집 중: {args.get('path', '')[:50]}",
        "skill_save":            f"🧩 skill 저장 중: /{args.get('name', '')}",
        "skill_delete":          f"🗑️  skill 삭제 중: /{args.get('name', '')}",
        "widget_save":           f"📊 위젯 저장 중: {args.get('title', '')}",
        "widget_delete":         f"🗑️  위젯 삭제 중: {args.get('widget_id', '')}",
        "kis_stock_quote":       f"📈 시세 조회 중: {args.get('symbol', '')}",
        "kis_account_query":     f"💰 계좌 조회 중…",
        "kis_market_ranking":    f"📊 시장 순위 조회 중…",
        "kis_order_execute":     f"⚠️  주문 실행 준비: {args.get('symbol', '')}",
        "xlsx_read":             f"📊 엑셀 읽는 중: {args.get('path', '')[:40]}",
        "docx_read":             f"📄 문서 읽는 중…",
        "sandbox_save_module":   f"💾 모듈 저장 중: {args.get('module_name', '')}",
        "image_generate":        f"{'🖼️  이미지 편집 중' if args.get('image_path') else '🎨 이미지 생성 중'}: {args.get('prompt', '')[:60]}",
    }.get(name, f"🔧 {name} 실행 중…")


def _exec_summary(command: str, stdout: str, err: str, rc) -> str:
    """host_exec / bash_exec 결과 한 줄 요약 — '무엇을 했는지'(명령어)를 표현.
    출력은 terminal 블럭에서 담당하므로 여기엔 명령어 + rc만."""
    cmd = " ".join((command or "").split())  # 개행/연속공백 압축
    if len(cmd) > 70:
        cmd = cmd[:70] + "…"
    failed = rc not in (0, None)
    prefix = "✗" if failed else "✓"
    if cmd:
        tail = f" · {err.splitlines()[0][:50]}" if (failed and err) else (f" rc={rc}" if failed else "")
        return f"{prefix} {cmd}{tail}"
    # 명령어 미상 폴백
    if err:
        return f"✗ {err.splitlines()[0][:80]}"
    return f"{prefix} 완료 (rc={rc})"


_EXEC_TERMINAL_MAX_LINES = 20  # 이보다 긴 출력은 terminal 블럭 생략 (긴 파일 목록 등)


def _build_aborted_message(partial_text: str, tool_trace: list[str]) -> str:
    """중단된 응답을 DB에 영속화할 텍스트로 조합.
    텍스트 토큰이 없어도 도구 실행 흔적이 있으면 보존 → 재방문 시 사라지지 않음.
    아무것도 없으면 빈 문자열(저장 안 함)."""
    parts = []
    if tool_trace:
        parts.append("\n".join(f"- {t}" for t in tool_trace))
    if partial_text.strip():
        parts.append(partial_text.strip())
    combined = "\n\n".join(parts).strip()
    if not combined:
        return ""
    return (combined + "\n\n_⏹ 응답이 중단됐습니다._").strip()


def _tool_summary(name: str, result: str, command: str = "") -> tuple[str, dict | None]:
    """(요약 텍스트, 차트 메타 dict | None) 반환.
    command: host_exec/bash_exec일 때 실행한 명령어 (summary에 표시)."""
    try:
        parsed = json.loads(result)
    except Exception:
        # 문자열을 직접 반환하는 도구(web_fetch/drive_read/file_read 등) — 이름 기반 요약.
        str_summaries = {
            "web_fetch":  "🌐 페이지 읽음",
            "drive_read": "📂 Drive 파일 읽음",
            "file_read":  "📄 파일 읽음",
            "file_edit":  "✏️ 파일 편집됨",
        }
        if isinstance(result, str) and result.lstrip().startswith(("실패", "fetch 실패", "오류", "error")):
            return (f"✗ {result.strip()[:80]}", None)
        return (f"✓ {str_summaries.get(name, name + ' 완료')}", None)

    if isinstance(parsed, dict) and parsed.get("__type") == "image":
        path = parsed.get("path", "")
        fname = Path(path).name if path else ""
        chart = {"type": "image", "url": f"/api/charts/{fname}"} if fname else None
        return ("✓ 차트 완성", chart)

    if isinstance(parsed, dict) and parsed.get("__type") == "html":
        path = parsed.get("path", "")
        fname = Path(path).name if path else ""
        chart = {"type": "html", "url": f"/api/charts/{fname}"} if fname else None
        return ("✓ 인터랙티브 차트 완성", chart)

    if isinstance(parsed, dict) and parsed.get("__needs_approval__"):
        cmd = parsed.get("command", "")[:120]
        return (f"⏸️  awaiting approval: `{cmd}`", None)

    if isinstance(parsed, dict) and "error" in parsed:
        return (f"✗ {str(parsed['error'])[:100]}", None)

    if isinstance(parsed, dict) and "stdout" in parsed:
        out = parsed["stdout"].strip()
        rc = parsed.get("returncode", 0)
        # 명령어를 요약으로, 출력은 terminal 블럭에서 표시
        err = parsed.get("stderr", "").strip() if rc not in (0, None) else ""
        cmd = command or parsed.get("command", "")
        summary = _exec_summary(cmd, out, err, rc)
        if parsed.get("warnings"):
            summary = "⚠️ " + " | ".join(parsed["warnings"][:2]) + " · " + summary
        return (summary, None)

    # 도구별 의미있는 완료 요약 — "무엇을 했고 결과가 뭔지" (rc=0 도배 방지).
    summary = _named_tool_summary(name, parsed)
    if summary:
        return (summary, None)

    if isinstance(parsed, list):
        return (f"✓ {len(parsed)}건", None)

    return ("✓ 완료", None)


# 도구 이름별 완료 요약 — result(파싱된 dict/list)에서 정량 정보를 뽑아 한 줄로.
# host_exec/bash_exec(stdout 분기)·차트·에러는 위에서 이미 처리되므로 여기선 나머지.
def _named_tool_summary(name: str, parsed) -> str | None:
    n = len(parsed) if isinstance(parsed, list) else None
    def pick(*keys):
        if isinstance(parsed, dict):
            for k in keys:
                if parsed.get(k) is not None:
                    return parsed[k]
        return None
    table = {
        "web_search":            lambda: f"🔍 검색 결과 {n}건" if n is not None else "🔍 검색 완료",
        "gmail_search":          lambda: f"📧 메일 {n}건" if n is not None else "📧 메일 검색 완료",
        "gmail_read":            lambda: f"📧 메일 읽음: {str(pick('subject') or '').strip()[:40]}",
        "gmail_send":            lambda: "📤 메일 전송됨",
        "gmail_draft":           lambda: "📝 초안 저장됨",
        "gmail_batch_modify":    lambda: f"🏷️ 메일 {pick('modified') or '?'}건 라벨 변경",
        "calendar_list_events":  lambda: f"📅 일정 {n}건" if n is not None else "📅 일정 확인 완료",
        "calendar_create_event": lambda: f"📅 일정 추가됨: {str(pick('summary') or '').strip()[:30]}",
        "calendar_delete_event": lambda: "📅 일정 삭제됨",
        "drive_search":          lambda: f"📂 Drive {n}건" if n is not None else "📂 Drive 검색 완료",
        "memory_persona_update": lambda: f"🧠 페르소나 저장 (v{pick('version') or '?'})",
        "memory_event_add":      lambda: f"📝 이벤트 기록됨 (#{pick('id') or '?'})",
        "memory_entity_upsert":  lambda: f"🗂️ 엔티티 {('생성' if pick('action')=='created' else '갱신')}됨",
        "skill_save":            lambda: f"🧩 skill 저장됨: /{str(pick('name') or '').strip()}",
        "skill_delete":          lambda: "🗑️ skill 삭제됨",
        "widget_save":           lambda: "📊 위젯 저장됨",
        "widget_delete":         lambda: "🗑️ 위젯 삭제됨",
        "xlsx_read":             lambda: f"📊 엑셀 읽음 ({pick('total_rows') or '?'}행)",
        "xlsx_create":           lambda: f"📊 엑셀 생성됨 ({pick('rows_written') or '?'}행)",
        "docx_read":             lambda: "📄 문서 읽음",
        "docx_create":           lambda: "📄 문서 생성됨",
        "pptx_read":             lambda: f"📑 슬라이드 {pick('slide_count') or '?'}장",
        "self_edit_file":        lambda: f"✏️ 파일 {('작성' if pick('action')=='write' else '편집')}됨",
        "system_info":           lambda: "💻 시스템 상태 확인됨",
        "session_list":          lambda: "📋 세션 목록 조회됨",
    }
    fn = table.get(name)
    if fn:
        try:
            return "✓ " + fn()
        except Exception:
            pass
    # 매핑 없는 도구: ok 키나 list 길이로 최소한의 정보 제공
    if isinstance(parsed, dict) and parsed.get("ok") is True:
        return f"✓ {name} 완료"
    if n is not None:
        return f"✓ {name}: {n}건"
    return None


import re as _re

# Tool progress narration lines — emitted by the model in-body or re-generated by mimicking
# the previous turn. e.g. "- ✓ 완료", "- ✗ ", "- ✓ browser_evaluate 완료", "- ✓ cd ... && find ...".
# Left in history, the model mimics them again next turn → self-echo snowball.
_TOOL_NARRATION_RE = _re.compile(r"^\s*[-*]\s*[✓✗⟳⏹]\s.*$")


def _slim_assistant_content(text: str) -> str:
    """Strip tool progress narration lines from an assistant reply to slim the next-turn prompt.
    Keep the model's explanation/conclusion text; remove only tool completion/command-echo lines.
    Removes the source of self-echo (mimicking the previous turn's tool summary) from history."""
    if not text or ("✓" not in text and "✗" not in text and "⟳" not in text and "⏹" not in text):
        return text
    kept = [ln for ln in text.split("\n") if not _TOOL_NARRATION_RE.match(ln)]
    slimmed = "\n".join(kept)
    return slimmed.strip("\n") if slimmed.strip() else text


def _format_db_context(hits: list[dict]) -> str:
    if not hits:
        return ""
    lines = [f"- [{r['event_date']}] {r['title']}: {r['body'][:150]}…" for r in hits]
    return "\n\n[관련 이벤트 DB 검색 결과]\n" + "\n".join(lines)


# ── Slash commands ────────────────────────────────────────────────────────────

def handle_slash(user_text: str, sid: str) -> dict | None:
    """Handle slash command → {"text": str} or {"text": str, "switch_session": sid} or None."""
    parts = user_text.split(None, 1)
    cmd = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/events":
        date_parts = args.split("~")
        start = date_parts[0].strip()
        end = date_parts[1].strip() if len(date_parts) > 1 else None
        rows = events_by_date(start, end)
        if not rows:
            return {"text": f"`{start}` 범위에 이벤트 없음."}
        lines = [f"## 이벤트 ({len(rows)}건)\n"]
        for r in rows:
            lines.append(f"- **{r['event_date']}** {r['title']}")
        return {"text": "\n".join(lines)}

    if cmd == "/who":
        name = args
        ent = get_entity(name)
        events = events_by_entity(name)
        if not ent and not events:
            return {"text": f"`{name}` 엔티티를 찾을 수 없음."}
        lines = []
        if ent:
            lines.append(f"## {ent['name']} ({ent['kind']})")
            if ent.get("notes"):
                lines.append(f"\n{ent['notes']}\n")
            lines.append(f"- 첫 등장: {ent.get('first_seen', '?')[:10]}")
            lines.append(f"- 마지막: {ent.get('last_seen', '?')[:10]}")
        if events:
            lines.append(f"\n### 연관 이벤트 ({len(events)}건)")
            for e in events[-10:]:
                lines.append(f"- **{e['event_date']}** {e['title']}")
        return {"text": "\n".join(lines)}

    if cmd == "/tag":
        rows = events_by_tag(args)
        if not rows:
            return {"text": f"`{args}` 태그 이벤트 없음."}
        lines = [f"## `{args}` 태그 이벤트 ({len(rows)}건)\n"]
        for r in rows:
            lines.append(f"- **{r['event_date']}** {r['title']}")
        return {"text": "\n".join(lines)}

    if cmd == "/search":
        rows = search_events(args, limit=20)
        if not rows:
            return {"text": f"`{args}` 검색 결과 없음."}
        lines = [f"## 검색: `{args}` ({len(rows)}건)\n"]
        for r in rows:
            lines.append(f"- **{r['event_date']}** {r['title']}")
        return {"text": "\n".join(lines)}

    if cmd == "/context":
        ctx = context_for_date(args)
        return {"text": ctx}

    if cmd == "/persona":
        content = get_persona(args or None)
        return {"text": content[:4000]}

    if cmd == "/sessions":
        sessions = list_sessions(limit=15)
        if not sessions:
            return {"text": "저장된 세션 없음."}
        lines = ["## 최근 세션\n"]
        for s in sessions:
            updated = s["updated_at"][:16].replace("T", " ")
            lines.append(
                f"- `{s['uuid'][:8]}…` **{s['name']}** — {s['msg_count']}개 메시지 ({updated})"
            )
        lines.append("\n`/resume <uuid앞8자리>` 로 이어서 대화")
        return {"text": "\n".join(lines)}

    if cmd == "/resume":
        sessions = list_sessions(limit=50)
        matched = [s for s in sessions if s["uuid"].startswith(args)]
        if not matched:
            return {"text": f"`{args}` 로 시작하는 세션 없음."}
        s = matched[0]
        history = load_history(s["uuid"])
        _SESSION_HISTORY[s["uuid"]] = history
        return {
            "text": f"세션 복원: **{s['name']}** ({len(history)}개 메시지)\n\n이어서 대화하면 됩니다.",
            "switch_session": s["uuid"],
        }

    if cmd == "/new":
        title = args or "VEGA 세션"
        new_sid = create_session(title)
        _SESSION_HISTORY[new_sid] = []
        return {
            "text": f"새 세션 시작: **{title}** (`{new_sid[:8]}…`)",
            "switch_session": new_sid,
        }

    if cmd == "/rename":
        rename_session(sid, args)
        return {"text": f"세션 이름 변경: **{args}**"}

    if cmd == "/plan":
        _PLAN_MODE[sid] = True
        return {"text": (
            "📋 **Plan 모드 켜짐.** 이제 코드 작성·실행·외부 전송 도구는 차단된다 "
            "(읽기·검색·DB 조회만 허용). 다음 메시지에서 요구사항을 분석하고 "
            "단계별 계획을 제시한 뒤, 준비가 되면 `exit_plan_mode` 도구로 승인 요청해라.\n\n"
            + (f"**Plan 모드 진입 요구사항:** {args}" if args else "이제 요구사항을 적어줘.")
        )}

    if cmd == "/plan-off":
        was = _PLAN_MODE.pop(sid, False)
        return {"text": "📋 Plan 모드 " + ("해제됨." if was else "이미 꺼져있었음.")}

    if cmd == "/rules":
        from pipeline.tools import _rule_list
        result = _rule_list()
        count = result.get("count", 0)
        if count == 0:
            return {"text": "📋 저장된 규칙 없음. '앞으로 이렇게 해줘'라고 말하면 VEGA가 규칙으로 저장합니다."}
        lines = [f"📋 **저장된 규칙 ({count}개)**\n"]
        current_section = None
        for r in result.get("rules", []):
            if r["section"] != current_section:
                current_section = r["section"]
                lines.append(f"\n**{current_section}**")
            lines.append(f"- `{r['rule_id']}` {r['rule_text']}")
        return {"text": "\n".join(lines)}

    if cmd == "/audit":
        # Tool performance telemetry — tools with most recent failures shown first
        from pipeline.tool_telemetry import summary, get_stats, get_recent_failures
        s = summary()
        stats = get_stats(limit=15, order_by="failures")
        fails = get_recent_failures(limit=10)
        lines = [
            f"🔍 **도구 텔레메트리 — 누적**\n",
            f"- 도구 수: {s['tool_count']} · 호출 {s['total_calls']:,} · 성공 {s['total_successes']:,} · 실패 {s['total_failures']:,}",
            f"- 전체 에러율: **{s['overall_error_rate']*100:.2f}%**\n",
        ]
        if stats:
            lines.append("**상위 도구 (실패 많은 순)**\n")
            lines.append("| 도구 | 호출 | 실패 | 에러율 | 평균 ms | 마지막 |")
            lines.append("|------|-----:|-----:|-------:|--------:|-------|")
            for st in stats:
                if st["calls"] == 0:
                    continue
                last = (st["last_called_ts"] or "")[:16]
                lines.append(
                    f"| `{st['name']}` | {st['calls']} | {st['failures']} | "
                    f"{st['error_rate']*100:.1f}% | {st['avg_ms']} | {last} |"
                )
        if fails:
            lines.append("\n**최근 실패 10건**\n")
            for f in fails:
                ts = (f.get("ts") or "")[:19]
                err = (f.get("error") or "").replace("\n", " ")[:120]
                lines.append(f"- `{ts}` `{f['name']}` — {err}")
        if not stats:
            lines.append("\n(아직 호출 기록 없음 — 도구를 사용하면 여기 누적됩니다.)")
        return {"text": "\n".join(lines)}

    if cmd == "/research":
        _RESEARCH_MODE[sid] = True
        return {"text": (
            "🔬 **Research 모드 켜짐.** 가설→근거→결론 3단계 추론, 웹 검색 우선, "
            "출처·불확실성 명시, 더 많은 도구 반복을 허용한다.\n\n"
            + (f"**연구 주제:** {args}" if args else "이제 연구할 주제를 적어줘.")
        )}

    if cmd == "/research-off":
        was = _RESEARCH_MODE.pop(sid, False)
        return {"text": "🔬 Research 모드 " + ("해제됨." if was else "이미 꺼져있었음.")}

    if cmd == "/yolo":
        _state_mod._YOLO_GLOBAL = True
        _save_yolo_global(True)
        return {"text": (
            "⚡ **YOLO 모드 켜짐 (전역).** 이제 모든 세션에서 host_exec의 allowlist 외 명령도 "
            "사용자 승인 없이 자동 실행한다. 단, 하드 차단(rm -rf /, mkfs, > /dev/ 등)과 시크릿 "
            "검사는 그대로 적용.\n\n"
            "AskUserQuestion(선택지), exit_plan_mode, self_improve 패치는 여전히 사용자 승인 필요.\n\n"
            "`/yolo-off`로 해제."
        )}

    if cmd == "/yolo-off":
        was = _state_mod._YOLO_GLOBAL or bool(_YOLO_MODE)
        _state_mod._YOLO_GLOBAL = False
        _YOLO_MODE.clear()
        _save_yolo_global(False)
        return {"text": "⚡ YOLO 모드 " + ("해제됨." if was else "이미 꺼져있었음.")}

    if cmd == "/help":
        return {"text": """## VEGA 커맨드

| 커맨드 | 예시 | 설명 |
|--------|------|------|
| `/events` | `/events 2026-03` | 날짜 범위 이벤트 |
| `/events` | `/events 2026-01 ~ 2026-03` | 기간 이벤트 |
| `/who` | `/who 이슬` | 인물/조직 프로필 + 타임라인 |
| `/tag` | `/tag mental_health` | 태그별 이벤트 |
| `/search` | `/search VocoNet` | 키워드 전문 검색 |
| `/context` | `/context 2026-02-15` | 날짜 전후 컨텍스트 |
| `/persona` | `/persona identity` | 페르소나 섹션 조회 |
| `/sessions` | `/sessions` | 저장된 세션 목록 |
| `/resume` | `/resume a1b2c3d4` | 이전 세션 복원 |
| `/new` | `/new 프로젝트 논의` | 새 세션 시작 |
| `/rename` | `/rename 새 제목` | 현재 세션 이름 변경 |
| `/plan` | `/plan 리팩터링 계획` | Plan 모드 진입 — 실행 도구 차단, 계획만 |
| `/plan-off` | `/plan-off` | Plan 모드 해제 |
| `/rules` | `/rules` | 저장된 행동 규칙 목록 보기 |
| `/audit` | `/audit` | 도구 텔레메트리 — 호출/실패 통계, 최근 실패 로그 |
| `/research` | `/research 주제` | Research 모드 — 웹검색 우선, 가설→근거→결론, 출처 명시 |
| `/research-off` | `/research-off` | Research 모드 해제 |
| `/yolo` | `/yolo` | YOLO 모드 — host_exec 자동 승인 (하드 차단·시크릿은 그대로) |
| `/yolo-off` | `/yolo-off` | YOLO 모드 해제 |
| `/help` | `/help` | 이 도움말 |

**도구 (자연어로 바로 사용):**
- 인터넷 검색: "요즘 ArtifactNet 논문 트렌드 찾아봐"
- Gmail: "unread 메일 10개 보여줘"
- 캘린더: "이번 주 일정 뭐야"
- Drive: "ArtifactNet 관련 파일 Drive에서 찾아봐"
""" + _custom_commands_help()}

    return None  # unrecognized → GPT path


def _custom_commands_help() -> str:
    """Append data/commands/*.md custom commands to the /help table."""
    try:
        from pipeline.commands import load_commands
        cmds = load_commands()
    except Exception:
        return ""
    if not cmds:
        return ""
    lines = ["\n**커스텀 커맨드 (data/commands/):**\n", "| 커맨드 | 설명 |", "|--------|------|"]
    for c in cmds.values():
        hint = f" {c.argument_hint}" if c.argument_hint else ""
        lines.append(f"| `/{c.name}{hint}` | {c.description or '—'} |")
    return "\n".join(lines)


# ── API routes ───────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return HTMLResponse((STATIC_DIR / "dashboard.html").read_text(encoding="utf-8"))


@app.get("/entry")
async def entry():
    """데스크톱 셸 진입점 — 온보딩 완료면 /chat, 아니면 설치 마법사(/install)로 보낸다."""
    from fastapi.responses import RedirectResponse
    from pipeline.user_profile import is_onboarded
    return RedirectResponse(url=("/chat" if is_onboarded() else "/install"), status_code=302)


@app.get("/install")
async def install_page():
    return HTMLResponse((STATIC_DIR / "install_wizard.html").read_text(encoding="utf-8"))


@app.get("/chat")
async def chat_page():
    return HTMLResponse((STATIC_DIR / "chat.html").read_text(encoding="utf-8"))


# OAuth, upload, STT 라우트 → web/routers/{oauth,upload,stt}.py 로 이전됨


@app.get("/api/health")
async def health():
    # 인증 상태는 *현재 active 프로바이더의 auth_type 에 맞춰* 판정한다.
    # 예전엔 프로바이더와 무관하게 ChatGPT OAuth 토큰만 봐서, OpenRouter 등
    # 키 기반 프로바이더를 써도 "No OAuth profile found" 가 떴다.
    auth_status = "ok"
    auth_remaining_min = 0
    active_name = ""
    try:
        from pipeline.llm_gateway import get_active_provider
        prov = get_active_provider()
        active_name = prov.get("name", "")
        auth_type = prov.get("auth_type", "")

        if auth_type == "chatgpt_oauth":
            from pipeline.auth.chatgpt import ensure_valid_token, _load_profile
            ensure_valid_token()  # 만료 임박 시 자동 갱신
            profile = _load_profile() or {}
            remains = profile.get("expires_at", 0) - int(time.time())
            auth_status = "ok"
            auth_remaining_min = max(0, remains // 60)
        elif auth_type in ("bearer", "anthropic_key"):
            from pipeline import keychain
            key_env = prov.get("api_key_env", "")
            has_key = bool(key_env and keychain.get(key_env))
            auth_status = "ok" if has_key else "API 키 미설정"
        elif auth_type == "none":
            auth_status = "ok"  # 로컬/온프레미스 — 키 불필요
        else:
            auth_status = "ok"
    except Exception as e:
        auth_status = str(e).split("\n")[0]  # 첫 줄만
        auth_remaining_min = 0
    from pipeline.mcp_client import _tool_server
    mcp_tools = len(_tool_server)

    # 코드 실행은 호스트 동봉 인터프리터로 직접 동작(Docker 제거, INT-1870). 별도 가용성 진단 불필요.
    sandbox_status = "host"

    # 전체 도구 개수(office/sandbox 포함) — TOOL_SCHEMAS 기준.
    try:
        from pipeline.tools import TOOL_SCHEMAS
        total_tools = len(TOOL_SCHEMAS)
    except Exception:
        total_tools = 0

    # 백엔드 정체성 — 같은 포트를 다른 VEGA 계열 백엔드(개인 VEGA=vega.db 등)와
    # 나눠 잡는 split-brain 사고 진단용. launcher 의 포트 가드도 이 필드를 본다.
    try:
        from pipeline.data_paths import db_path
        db_name = db_path().name
    except Exception:
        db_name = "?"

    return JSONResponse({
        "status": "ok",
        "app": "vega-agent",
        "db": db_name,
        "auth": auth_status,
        "auth_remaining_min": auth_remaining_min,
        "active_provider": active_name,
        "mcp_tools": mcp_tools,
        "total_tools": total_tools,
        "sandbox": sandbox_status,
    })


@app.post("/api/approve")
async def approve_command(req: Request):
    """Handle approval button click. Re-runs host_exec or applies tool patch depending on type."""
    body = await req.json()
    approved = body.get("approved", False)
    approve_type = body.get("type", "command")  # "command" | "improvement"
    sid = body.get("sid", "")

    if approve_type == "improvement":
        if not approved:
            return JSONResponse({"ok": False, "result": "거절됨"})
        tool_name = body.get("tool_name", "")
        patch_code = body.get("patch_code", "")
        if not tool_name or not patch_code:
            return JSONResponse({"ok": False, "result": "tool_name 또는 patch_code 누락"})
        try:
            from pipeline.self_improve import apply_patch
            result = apply_patch(tool_name, patch_code)
            return JSONResponse(result)
        except Exception as e:
            return JSONResponse({"ok": False, "result": str(e)})

    if approve_type == "question":
        # AskUserQuestion answer — answers: {question: label} or {question: [labels]}
        answers = body.get("answers") or {}
        reg = _TASK_REGISTRY.get(sid)
        if reg and "approval_queue" in reg:
            await reg["approval_queue"].put({"answers": answers})
        return JSONResponse({"ok": True})

    if approve_type == "plan":
        # /plan exit_plan_mode approval/rejection
        reg = _TASK_REGISTRY.get(sid)
        if reg and "approval_queue" in reg:
            await reg["approval_queue"].put({"approved": bool(approved)})
        return JSONResponse({"ok": True, "approved": bool(approved)})

    if approve_type == "consent":
        # Permission consent gate (INT-1386) — only the approval decision; streaming dispatches.
        reg = _TASK_REGISTRY.get(sid)
        if reg and "approval_queue" in reg:
            await reg["approval_queue"].put({"approved": bool(approved)})
        return JSONResponse({"ok": True, "approved": bool(approved)})

    # host_exec approval — execute, then push result to session approval_queue → resumes VEGA loop
    command = body.get("command", "")
    call_id = body.get("call_id", "")
    reg = _TASK_REGISTRY.get(sid)

    if not approved:
        # Rejected: push rejection signal to queue + pending 정리
        if reg:
            reg.get("_pending_approvals", {}).pop(call_id, None)
            if "approval_queue" in reg:
                await reg["approval_queue"].put({"approved": False, "result": None})
        return JSONResponse({"ok": False, "result": "거절됨"})

    # 무결성 (INT-2231): body 의 command 를 그대로 실행하지 않는다. 모델 요청 시 call_id 로
    # 저장해 둔 pending command 만 실행 — 일치하는 pending 이 없으면 거부(임의 명령 실행 차단).
    pending = reg.get("_pending_approvals", {}) if reg else {}
    server_cmd = None
    if call_id and call_id in pending:
        server_cmd = pending.pop(call_id)
    elif command:
        for cid, c in list(pending.items()):
            if c == command:
                server_cmd = pending.pop(cid)
                break
    if server_cmd is None:
        return JSONResponse(
            {"ok": False, "result": "승인 대기 중인 명령과 일치하지 않습니다"}, status_code=400
        )
    command = server_cmd

    try:
        from pipeline.tools_code import host_exec
        exec_result = host_exec(command, ask="off")
    except Exception as e:
        exec_result = {"error": str(e)}

    if reg and "approval_queue" in reg:
        await reg["approval_queue"].put({"approved": True, "result": exec_result})

    return JSONResponse({"ok": True, "result": exec_result})


@app.post("/api/abort")
async def abort_task(req: Request):
    """Actually cancel the in-progress GPT task. If awaiting approval, wake it with a rejection signal."""
    body = await req.json()
    sid = body.get("sid", "")
    reg = _TASK_REGISTRY.get(sid)
    if not reg:
        return JSONResponse({"ok": False, "reason": "no_task"})
    if reg.get("done"):
        return JSONResponse({"ok": False, "reason": "already_done"})

    # If stalled waiting for approval, insert rejection signal first to wake the await
    aq = reg.get("approval_queue")
    if aq is not None and aq.empty():
        try:
            aq.put_nowait({"approved": False, "result": None, "__aborted__": True})
        except Exception:
            pass

    task = reg.get("task")
    if task and not task.done():
        task.cancel()
    return JSONResponse({"ok": True})


# sessions 라우트 → web/routers/sessions.py 로 이전됨


@app.get("/api/context/preview")
async def context_preview(sid: str):
    """Per-category summary of the context that will be sent to the LLM for the next message.
    Calls build_system + load_history directly and returns size/token estimates with content preview."""
    from pipeline.streaming import build_system, _build_dashboard_context, _PERSONA_CACHE  # type: ignore
    from pipeline.vega_query import get_persona
    from pipeline.compaction import _estimate_tokens
    from pipeline.token_count import count_tokens, count_json_tokens
    from pipeline.llm_gateway import filter_tools, _to_chat_completions_tools
    from pipeline import tools as _tools

    sections: list[dict] = []

    # 1. Persona
    try:
        persona = get_persona() or ""
    except Exception as e:
        persona = ""
        sections.append({"key": "persona", "label": "페르소나", "error": str(e)})
    if persona:
        sections.append({
            "key": "persona", "label": "페르소나",
            "chars": len(persona), "tokens": count_tokens(persona),
            "preview": persona[:400],
        })

    # 2. Dashboard briefing (build_system injects this every message — cache miss expected)
    try:
        dashboard = _build_dashboard_context() or ""
    except Exception as e:
        dashboard = ""
    if dashboard:
        sections.append({
            "key": "dashboard", "label": "현재 상황 브리핑",
            "chars": len(dashboard), "tokens": count_tokens(dashboard),
            "preview": dashboard[:400],
            "capped": len(dashboard) >= 3000,
        })

    # 3. Working directory
    wd = get_working_dir(sid)
    if wd:
        sections.append({
            "key": "workdir", "label": "작업 폴더",
            "chars": len(wd), "tokens": count_tokens(wd),
            "preview": wd,
        })

    # 4. Session history
    try:
        history = load_history(sid) or []
    except Exception:
        history = []
    hist_tokens = _estimate_tokens(history)
    hist_chars = sum(len(str(m.get("content", ""))) for m in history)
    sections.append({
        "key": "history", "label": "대화 히스토리",
        "chars": hist_chars, "tokens": hist_tokens,
        "count": len(history),
        "preview": f"메시지 {len(history)}개",
    })

    # 5. Tool schemas (active group only)
    try:
        # 미연결 워크스페이스 도구 제외 — 실제 LLM 페이로드(streaming.py 경유)와 동일 기준
        from pipeline.tool_registry import filter_available_schemas
        active_tools = filter_tools(filter_available_schemas(_tools.TOOL_SCHEMAS))
        cc_tools = _to_chat_completions_tools(active_tools)
        tools_tokens = count_json_tokens(cc_tools)
        n_active = len(active_tools)
        n_total = len(_tools.TOOL_SCHEMAS)
    except Exception:
        active_tools = []
        tools_tokens = 0
        n_active = n_total = 0
    try:
        from pipeline.mcp_client import _tool_cache as _mcp_cache
        n_mcp = sum(len(v) for v in _mcp_cache.values())
    except Exception:
        n_mcp = 0
    sections.append({
        "key": "tools", "label": "도구 스키마",
        "chars": 0, "tokens": tools_tokens,
        "preview": f"활성 {n_active}/{n_total}개 + MCP {n_mcp}개",
        "n_active": n_active, "n_total": n_total, "n_mcp": n_mcp,
    })

    # Full system prompt (what actually gets sent to the LLM)
    try:
        full_system = build_system(wd)
    except Exception as e:
        full_system = ""
    sys_tokens = count_tokens(full_system)

    # Per-round prefix: system + tools. History accumulates separately each round.
    round_overhead = sys_tokens + tools_tokens

    return JSONResponse({
        "sections": sections,
        "system_prompt_chars": len(full_system),
        "system_prompt_tokens": sys_tokens,
        "tools_tokens": tools_tokens,
        "round_overhead_tokens": round_overhead,
        "total_tokens_estimate": round_overhead + hist_tokens,
        "session_id": sid,
    })




# LLM/MCP router moved to web/routers/llm.py




# sessions, admin 라우트 → web/routers/{sessions,admin}.py 로 이전됨


# ── Core SSE streaming endpoint ───────────────────────────────────────────────

def _push_event(reg: dict, event: dict) -> None:
    """Append event to the task registry and wake the consumer."""
    reg["buf"].append(event)
    reg["last_activity"] = time.monotonic()  # for watchdog — detect inactivity hang
    reg["consumer"].set()


def _resume_stalled_session(sid: str) -> None:
    """Resume a stalled YOLO session with a 'continue' turn on a fresh task.
    Discard the old reg and replace — cleanly replaces a dead/disconnected task."""
    history = _get_history(sid)
    resume_msg = ("(시스템: 이 작업이 한동안 멈춰 있었다. 중단 지점부터 이어서 계속 진행해. "
                  "이미 끝난 작업을 반복하지 말고 남은 것만 마저 하고, 다 끝나면 완료를 명시해.)")
    if not (history and history[-1].get("role") == "user" and history[-1].get("content") == resume_msg):
        history.append({"role": "user", "content": resume_msg})
    _heartbeat_resumes[sid] = _heartbeat_resumes.get(sid, 0) + 1
    reg = {
        "task": None,
        "buf": [],
        "done": False,
        "consumer": asyncio.Event(),
        "approval_queue": asyncio.Queue(),
        "last_activity": time.monotonic(),
        "awaiting_approval": False,
        "_heartbeat_resumed": True,
    }
    _TASK_REGISTRY[sid] = reg
    reg["task"] = asyncio.create_task(_run_gpt_task(sid, history, []))
    print(f"[heartbeat] resumed stalled YOLO session: {sid[:8]} "
          f"(resume {_heartbeat_resumes[sid]}/{HEARTBEAT_MAX_RESUMES})")


# Whole-task done signals — the work itself is finished. Plain '완료.' is excluded
# (prevents reading a partial-completion report as full completion).
_DONE_MARKERS = [
    "모든 작업 완료", "모든 작업을 완료", "작업 전체 완료", "전체 작업 완료",
    "모든 chunk 완료", "전부 완료했", "전부 끝냈", "모두 끝냈", "모두 완료했",
    "최종 완료", "작업을 마쳤", "작업 종료", "더 할 일 없", "더 이상 할 일",
    "남은 작업 없", "남은 게 없", "all done", "all complete", "fully complete",
    "✅ 전체", "🎉 완료",
]
# Progress signals — if present, it's partial/in-progress, not finished.
_PROGRESS_MARKERS = [
    "남은", "남았", "다음", "이어서", "계속", "아직", "진행 중", "진행중",
    "todo", "to-do", "chunk0", "chunk 0", "part0", "next:", "남겨", "마저",
    "이제", "재개", "중단 지점", "checkpoint", "체크포인트",
]


def _autopilot_looks_done(sid: str) -> bool:
    """True if the autopilot session finished the WHOLE task. Prevents infinite revive.
    Avoids false positives: a 'chunk01/02 done, rest remaining' report is NOT full completion.
    → done only when an explicit whole-task signal is present AND no progress signal."""
    try:
        from pipeline.session_store import load_history
        hist = load_history(sid)
    except Exception:
        return False
    for m in reversed(hist):
        if m.get("role") == "assistant":
            text = (m.get("content") or "")[-500:].lower()
            has_done = any(mk.lower() in text for mk in _DONE_MARKERS)
            if not has_done:
                return False
            has_progress = any(mk.lower() in text for mk in _PROGRESS_MARKERS)
            return not has_progress
    return False


def _resume_autopilot_db(sid: str) -> bool:
    """Revive an autopilot session by DB without a memory reg. Core of restart-surviving autonomy.
    Returns True if actually resumed."""
    data = _load_autopilot()
    info = data.get(sid)
    if info is None:
        return False
    if info.get("resumes", 0) >= HEARTBEAT_DB_MAX_RESUMES:
        _autopilot_unregister(sid)
        print(f"[heartbeat] autopilot {sid[:8]} hit resume cap → untracked")
        return False
    if _autopilot_looks_done(sid):
        _autopilot_unregister(sid)
        print(f"[heartbeat] autopilot {sid[:8]} looks done → untracked")  # cxt-ignore: fake_execution
        return False
    _resume_stalled_session(sid)
    info["resumes"] = info.get("resumes", 0) + 1
    data[sid] = info
    _save_autopilot(data)
    print(f"[heartbeat] autopilot DB revive: {sid[:8]} (cumulative {info['resumes']}/{HEARTBEAT_DB_MAX_RESUMES})")
    return True


async def _cron_loop() -> None:
    """Run scheduled cron jobs at their due time (INT-1407). Checks due_jobs every 60s,
    spawns a new session + prompt via _run_gpt_task for each. Auto-progresses like YOLO."""
    while True:
        try:
            await asyncio.sleep(60)
            from pipeline import cron_jobs
            for job in cron_jobs.due_jobs():
                try:
                    prefix = "🤖" if job.get("is_slot") else "⏱"
                    icon = job.get("icon", prefix) if job.get("is_slot") else prefix
                    sid = create_session(f"{icon} {job.get('label', 'cron')}")
                    append_message(sid, "user", job["prompt"])
                    history = [{"role": "user", "content": job["prompt"]}]
                    reg = {
                        "task": None, "buf": [], "done": False,
                        "consumer": asyncio.Event(), "approval_queue": asyncio.Queue(),
                        "last_activity": time.monotonic(), "awaiting_approval": False,
                        "_cron_job": job["id"],
                    }
                    _TASK_REGISTRY[sid] = reg
                    reg["task"] = asyncio.create_task(_run_gpt_task(sid, history, []))
                    cron_jobs.mark_run(job["id"], "started", session_id=sid)
                    print(f"[cron] run: {job['label']} → session {sid[:8]}")
                except Exception as e:
                    print(f"[cron] job failed({job.get('id')}): {e}")
                    try:
                        cron_jobs.mark_run(job["id"], f"error: {e}")
                    except Exception:
                        pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[cron] loop warning: {e}")


async def _yolo_heartbeat() -> None:
    """Periodically find stalled YOLO sessions and auto-resume. Started once from lifespan.
    self-wake refreshes last_activity inside the live task, so it won't hit the 5min idle
    condition → heartbeat only catches dead or truly stalled sessions (no double resume).
    Two layers: (1) memory-reg stalled YOLO sessions (2) autopilot sessions revive by DB
    even without a reg (survives restart)."""
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                from pipeline.heartbeat import run_heartbeat_periodic_work

                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, run_heartbeat_periodic_work)
            except Exception as e:
                print(f"[heartbeat] periodic work warning: {e}")
            now = time.monotonic()
            # (1) memory-reg based — live-then-stalled YOLO sessions
            stalled = [
                sid for sid, reg in list(_TASK_REGISTRY.items())
                if _is_stalled_yolo(sid, reg, now)
            ]
            for sid in stalled:
                try:
                    _resume_stalled_session(sid)
                except Exception as e:
                    print(f"[heartbeat] resume failed {sid[:8]}: {e}")
            # (2) autopilot sessions — revive by DB if not running in memory (survives restart)
            for sid in list(_load_autopilot().keys()):
                reg = _TASK_REGISTRY.get(sid)
                live = reg and not reg.get("done") and \
                    (now - reg.get("last_activity", now)) < HEARTBEAT_STALL_SEC
                if live:
                    continue
                try:
                    _resume_autopilot_db(sid)
                except Exception as e:
                    print(f"[heartbeat] autopilot revive failed {sid[:8]}: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[heartbeat] scan error: {e}")


# 백그라운드 compaction 상태 — sid 단위 중복 실행 방지 + 완료 알림 이연 전달
_COMPACTING: set[str] = set()
_PENDING_COMPACT_NOTICE: dict[str, str] = {}


async def _run_gpt_task(sid: str, history: list[dict], images: list[dict]) -> None:
    """
    Background GPT task fully decoupled from the connection.
    Appends events to _TASK_REGISTRY[sid]["buf"] and signals via consumer Event.
    Task runs to completion even if the client disconnects.
    """
    reg = _TASK_REGISTRY[sid]
    # 직전 턴 사이에 백그라운드 압축이 끝났으면 알림 배너를 이번 스트림 머리에 싣는다
    _pending_summary = _PENDING_COMPACT_NOTICE.pop(sid, None)
    if _pending_summary:
        _push_event(reg, {"event": "compacted", "data": {"status": "done", "summary": _pending_summary}})
    partial: list[str] = []
    # 인터리빙 events — 텍스트 세그먼트와 도구 호출을 시간순으로 누적.
    # 콜백 호출 순서 = 라이브 SSE 순서이므로 재방문 복원이 라이브와 일치.
    events: list[dict] = []

    def _append_text_event(tok: str):
        """마지막 event가 텍스트면 이어붙이고, 아니면(도구 뒤면) 새 텍스트 세그먼트를 연다."""
        if events and events[-1].get("type") == "text":
            events[-1]["data"] += tok
        else:
            events.append({"type": "text", "data": tok})

    async def on_waiting():
        _push_event(reg, {"event": "thinking", "data": {"label": "생각 중…"}})

    async def on_token(tok: str):
        partial.append(tok)
        _append_text_event(tok)
        _push_event(reg, {"event": "token", "data": {"token": tok}})

    async def on_reasoning(delta: str, done: bool = False):
        _push_event(reg, {"event": "reasoning", "data": {"delta": delta, "done": done}})

    async def on_tool_start(name: str, args: dict, call_id: str = ""):
        label = _tool_label(name, args)
        reg["tool_count"] = reg.get("tool_count", 0) + 1
        # call_id → args 매핑 저장 (on_tool_done에서 명령어 요약에 사용)
        if call_id:
            reg.setdefault("_tool_args", {})[call_id] = args
        # args를 압축해서 전달 (UI 펼쳐보기용) — 긴 값은 자름
        try:
            _MAX_VAL = 200
            clipped = {
                k: (v[:_MAX_VAL] + "…(생략)" if isinstance(v, str) and len(v) > _MAX_VAL else v)
                for k, v in args.items()
            }
            args_preview = json.dumps(clipped, ensure_ascii=False, indent=2)
            if len(args_preview) > 2000:
                args_preview = args_preview[:2000] + "\n… (생략)"
        except Exception:
            args_preview = str(args)[:2000]
        _push_event(reg, {"event": "tool_start", "data": {
            "name": name, "label": label, "call_id": call_id,
            "step": reg["tool_count"], "args": args_preview,
        }})
        # 인터리빙 events에 도구 항목 추가 (on_tool_done에서 summary/status 채움)
        events.append({
            "type": "tool", "name": name, "label": label,
            "call_id": call_id, "step": reg["tool_count"],
            "args": args_preview, "status": "started",
        })
        # host_exec 실시간 출력 스트리밍 콜백 등록
        if name == "host_exec":
            loop = asyncio.get_event_loop()
            def _host_line_cb(tag: str, line: str):
                # Called from background thread → thread-safe push
                loop.call_soon_threadsafe(
                    _push_event, reg,
                    {"event": "exec_output", "data": {"call_id": call_id, "tag": tag, "line": line}},
                )
            try:
                from pipeline.tools_code import _HOST_EXEC_LINE_CB
                _HOST_EXEC_LINE_CB.set(_host_line_cb)
                reg.setdefault("_host_exec_cb_tokens", {})[call_id] = _HOST_EXEC_LINE_CB
            except Exception:
                pass
        # Persist run_log entry (RES-224)
        try:
            import time as _t
            from pipeline.run_log import record_start
            # sid: _run_gpt_task 클로저 변수 — session_id 오타(NameError)가
            # except pass에 삼켜져 run_log가 한 번도 기록되지 않던 버그 (INT-1487 점검 발견)
            row_id = record_start(sid, name, args, call_id)
            reg.setdefault("_run_log", {})[call_id] = (row_id, _t.monotonic())
        except Exception:
            pass

    async def on_consent(name: str, args: dict, call_id: str = "") -> bool:
        """Permission consent gate (INT-1386). Pushes a consent card with a level badge
        and awaits the decision via approval_queue. Auto-approves in YOLO mode."""
        if _yolo_on(sid):
            return True
        try:
            from pipeline.permission import level_meta
            meta = level_meta(name)
        except Exception:
            meta = {"level": "WRITE", "label": "쓰기", "color": "#3b82f6", "badge": "W"}
        try:
            args_preview = json.dumps(args, ensure_ascii=False)[:400]
        except Exception:
            args_preview = str(args)[:400]
        _push_event(reg, {"event": "consent", "data": {
            "call_id": call_id, "name": name, "label": _tool_label(name, args),
            "args": args_preview, "level": meta,
        }})
        reg["awaiting_approval"] = True
        reg["awaiting_since"] = time.time()
        try:
            decision = await reg["approval_queue"].get()
        finally:
            reg["awaiting_approval"] = False
            reg.pop("awaiting_since", None)
        if decision.get("__aborted__"):
            raise asyncio.CancelledError()
        return bool(decision.get("approved", False))

    async def on_tool_done(name: str, result: str, call_id: str = "") -> str | None:
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("__needs_approval__"):
                command = parsed.get("command", "")
                # YOLO mode: skip approval wait and re-run immediately with ask="off".
                # Hard-block and secret checks apply inside host_exec regardless of ask mode — safe.
                if _yolo_on(sid):
                    from pipeline.tools_code import host_exec as _host_exec
                    _push_event(reg, {"event": "tool_start", "data": {
                        "call_id": f"{call_id}-yolo", "name": "host_exec",
                        "label": "⚡ YOLO 자동 실행",
                        "args": {"command": command},
                    }})
                    yolo_result = await loop.run_in_executor(None, _host_exec, command, "off")
                    actual = json.dumps(yolo_result, ensure_ascii=False)
                    stdout = (yolo_result.get("stdout") or "").strip()
                    err = yolo_result.get("error") or yolo_result.get("stderr") or ""
                    summary = _exec_summary(command, stdout, err.strip(), yolo_result.get("returncode", 0))
                    _push_event(reg, {"event": "tool_done", "data": {
                        "call_id": f"{call_id}-yolo", "name": "host_exec",
                        "summary": summary,
                    }})
                    return actual
                # 승인 무결성 (INT-2231): /api/approve 가 body 의 임의 command 를 실행하지
                # 못하도록, 모델이 요청한 command 를 call_id 로 서버에 저장해 둔다.
                reg.setdefault("_pending_approvals", {})[call_id] = command
                _push_event(reg, {"event": "approval", "data": {
                    "call_id": call_id, "name": name,
                    "command": command,
                    "reason": parsed.get("reason", ""),
                }})
                # Wait for user response (approved: bool, result: dict|None)
                # Watchdog exempt: indefinite wait for approval is normal
                reg["awaiting_approval"] = True
                reg["awaiting_since"] = time.time()
                try:
                    approval = await reg["approval_queue"].get()
                finally:
                    reg["awaiting_approval"] = False
                    reg.pop("awaiting_since", None)
                if approval.get("__aborted__"):
                    raise asyncio.CancelledError()
                approved = approval.get("approved", False)
                exec_result = approval.get("result")
                if approved and exec_result is not None:
                    actual = json.dumps(exec_result, ensure_ascii=False)
                else:
                    actual = json.dumps({"status": "거절됨", "output": "사용자가 실행을 거절했습니다."}, ensure_ascii=False)
                # Update tool_done badge
                if approved and exec_result:
                    stdout = (exec_result.get("stdout") or "").strip()
                    err = exec_result.get("error") or exec_result.get("stderr") or ""
                    summary = _exec_summary(command, stdout, err.strip(), exec_result.get("returncode", 0))
                else:
                    summary = "✗ 거절됨"
                done_data: dict = {"name": name, "summary": summary, "has_chart": False, "call_id": call_id}
                if approved and exec_result and ("stdout" in exec_result or "stderr" in exec_result):
                    stdout = (exec_result.get("stdout") or "").strip()
                    stderr = (exec_result.get("stderr") or "").strip()
                    combined = (stdout + ("\n" + stderr if stderr else "")).strip()
                    rc = exec_result.get("returncode", 0)
                    n_lines = combined.count("\n") + 1 if combined else 0
                    if combined and (rc not in (0, None) or n_lines <= _EXEC_TERMINAL_MAX_LINES):
                        done_data["terminal"] = combined
                        done_data["returncode"] = rc
                _push_event(reg, {"event": "tool_done", "data": done_data})
                return actual  # streaming.py uses this as function_call_output
            if isinstance(parsed, dict) and parsed.get("__improvement_pending__"):
                _push_event(reg, {"event": "improvement", "data": {
                    "tool_name": parsed["tool_name"],
                    "diff": parsed.get("diff", ""),
                    "patch_code": parsed.get("patch_code", ""),
                    "failures": parsed.get("failures", 0),
                    "test_output": parsed.get("test_output", ""),
                }})
                return
            # 자동 적용 완료 통지 (auditor 통과 후 자동 적용 — 승인 대기 아님)
            if isinstance(parsed, dict) and parsed.get("__improvement_applied__"):
                _push_event(reg, {"event": "improvement_applied", "data": {
                    "tool_name": parsed["tool_name"],
                    "diff": parsed.get("diff", ""),
                    "failures": parsed.get("failures", 0),
                    "audit_reason": parsed.get("audit_reason", ""),
                }})
                return
            # AskUserQuestion — UI 위젯으로 변환 후 응답 대기
            if isinstance(parsed, dict) and parsed.get("__needs_user_answer__"):
                questions = parsed.get("questions", [])
                _push_event(reg, {"event": "question", "data": {
                    "call_id": call_id, "name": name,
                    "questions": questions,
                }})
                reg["awaiting_approval"] = True
                reg["awaiting_since"] = time.time()
                try:
                    answer = await reg["approval_queue"].get()
                finally:
                    reg["awaiting_approval"] = False
                    reg.pop("awaiting_since", None)
                if answer.get("__aborted__"):
                    raise asyncio.CancelledError()
                answers = answer.get("answers") or {}
                # tool_done badge: use first question's selected label as summary
                if answers:
                    first_q = questions[0]["question"] if questions else ""
                    first_a = answers.get(first_q, "")
                    if isinstance(first_a, list):
                        first_a = ", ".join(first_a)
                    summary = f"✓ {(first_a or '응답')[:120]}"
                else:
                    summary = "✗ 응답 없음"
                _push_event(reg, {"event": "tool_done", "data": {
                    "name": name, "summary": summary, "has_chart": False, "call_id": call_id,
                }})
                return json.dumps({"answers": answers}, ensure_ascii=False)
            # /plan exit approval
            if isinstance(parsed, dict) and parsed.get("__needs_plan_approval__"):
                plan_text = parsed.get("plan", "")
                _push_event(reg, {"event": "plan_approval", "data": {
                    "call_id": call_id, "name": name, "plan": plan_text,
                }})
                reg["awaiting_approval"] = True
                reg["awaiting_since"] = time.time()
                try:
                    decision = await reg["approval_queue"].get()
                finally:
                    reg["awaiting_approval"] = False
                    reg.pop("awaiting_since", None)
                if decision.get("__aborted__"):
                    raise asyncio.CancelledError()
                approved = decision.get("approved", False)
                if approved:
                    _PLAN_MODE[sid] = False
                    summary = "✓ 계획 승인됨 — plan 모드 해제"
                    payload = {"approved": True, "message": "사용자가 계획을 승인했다. plan 모드가 해제되었으니 이제 계획대로 실제 도구를 호출해 실행해라."}
                else:
                    summary = "✗ 계획 거절됨"
                    payload = {"approved": False, "message": "사용자가 계획을 거절했다. 추가 질문이나 수정 제안을 해라. plan 모드는 유지된다."}
                _push_event(reg, {"event": "tool_done", "data": {
                    "name": name, "summary": summary, "has_chart": False, "call_id": call_id,
                }})
                return json.dumps(payload, ensure_ascii=False)
        except Exception:
            pass
        # __improve__<name> virtual tool events are not shown in the UI
        if name.startswith("__improve__"):
            return
        # 실행한 명령어 복원 (summary에 표시)
        _cmd = ""
        _saved_args = reg.get("_tool_args", {}).get(call_id) if call_id else None
        if isinstance(_saved_args, dict):
            _cmd = _saved_args.get("command", "")
        summary, chart = _tool_summary(name, result, command=_cmd)
        done_data: dict = {"name": name, "summary": summary, "has_chart": chart is not None, "call_id": call_id}
        if chart:
            done_data["chart_type"] = chart["type"]
            done_data["chart_url"] = chart["url"]
        # 코드 실행 stdout/stderr 전달 — 짧은 출력만 터미널 블럭으로 (긴 파일 목록 등은 생략)
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and ("stdout" in parsed or "stderr" in parsed):
                stdout = (parsed.get("stdout") or "").strip()
                stderr = (parsed.get("stderr") or "").strip()
                combined = (stdout + ("\n" + stderr if stderr else "")).strip()
                # 에러(rc≠0)면 항상 표시, 정상이면 짧을 때만
                rc = parsed.get("returncode", 0)
                n_lines = combined.count("\n") + 1 if combined else 0
                show = combined and (rc not in (0, None) or n_lines <= _EXEC_TERMINAL_MAX_LINES)
                if show:
                    done_data["terminal"] = combined
                    done_data["returncode"] = rc
        except Exception:
            pass
        # 중단 시 부분 응답 복원용 — 완료된 도구 요약을 누적
        reg.setdefault("_tool_trace", []).append(summary)
        # 인터리빙 events의 해당 도구 항목을 완료 상태로 업데이트 (call_id 매칭).
        # 재방문 시 도구 배지가 완료 모습(요약·터미널·차트)으로 복원되도록.
        for _ev in reversed(events):
            if _ev.get("type") == "tool" and _ev.get("call_id") == call_id:
                _ev["summary"] = summary
                _ev["status"] = "error" if summary.startswith("✗") else "done"
                if done_data.get("terminal") is not None:
                    _ev["terminal"] = done_data["terminal"]
                    _ev["returncode"] = done_data.get("returncode")
                if chart:
                    _ev["chart_type"] = chart["type"]
                    _ev["chart_url"] = chart["url"]
                break
        _push_event(reg, {"event": "tool_done", "data": done_data})
        # Deregister host_exec callback
        try:
            from pipeline.tools_code import _HOST_EXEC_LINE_CB
            _HOST_EXEC_LINE_CB.set(None)
        except Exception:
            pass
        # Record run_log completion (RES-224)
        try:
            from pipeline.run_log import record_done
            entry = reg.get("_run_log", {}).get(call_id)
            if entry:
                row_id, started_at = entry
                record_done(row_id, result, started_at=started_at)
        except Exception:
            pass

    # Watchdog: if no activity after last event for a long time, treat as hang and cancel main task.
    # Exempt while awaiting approval (indefinite user input is normal). Even long-running tools
    # keep producing token/tool events — only genuine stuck states are caught.
    # Threshold is mode-dependent (normal 60s / yolo·goal·research 300s).
    #
    # YOLO is unattended auto-run, so the user can't press retry. So even when a hang is cut,
    # don't end with error immediately — self-wake once to auto-resume and let it tell whether
    # it was a transient delay or a real stuck. If it hangs again after resume, then end.
    WATCHDOG_IDLE = _watchdog_idle_for(sid)
    SELF_WAKE_MAX = 1 if _yolo_on(sid) else 0  # only yolo allows auto-resume

    async def _watchdog(target_task: asyncio.Task):
        while not target_task.done():
            await asyncio.sleep(15)
            if reg.get("awaiting_approval"):
                continue
            idle = time.monotonic() - reg.get("last_activity", time.monotonic())
            if idle > WATCHDOG_IDLE and not target_task.done():
                woke = reg.get("_self_wake_count", 0)
                if SELF_WAKE_MAX and woke < SELF_WAKE_MAX:
                    # yolo self-wake: cut but leave a resume signal. Don't end with error.
                    reg["_self_wake_count"] = woke + 1
                    reg["_self_wake_pending"] = int(idle)
                    _push_event(reg, {"event": "thinking", "data": {
                        "label": f"{int(idle)}초 멈춰서 자동 재개 중…"}})
                    reg["last_activity"] = time.monotonic()  # avoid immediate re-cut after resume
                    target_task.cancel()
                    return
                _push_event(reg, {"event": "error", "data": {
                    "message": f"응답이 {int(idle)}초간 멈춰서 중단했어. 다시 시도해줘."}})
                target_task.cancel()
                return

    try:
        loop = asyncio.get_event_loop()
        wdir = get_working_dir(sid)
        system_prompt = await loop.run_in_executor(None, build_system, wdir)
        if _PLAN_MODE.get(sid):
            system_prompt = _PLAN_MODE_GUIDE + "\n\n---\n\n" + system_prompt
        if _RESEARCH_MODE.get(sid):
            system_prompt = _RESEARCH_MODE_GUIDE + "\n\n---\n\n" + system_prompt
        if _GOAL_MODE.get(sid):
            system_prompt = _GOAL_MODE_GUIDE + "\n\n---\n\n" + system_prompt
        usage_stats: dict = {}
        _load_ov = _LOAD_MODE.get(sid)
        if _load_ov == "fast":
            _load_ov = "light"
        parent_spawn_context = {
            "parent_session_id": sid,
            "parent_agent_id": None,
            "parent_reg": reg,
            "loop": loop,
            "working_dir": wdir,
            "plan_mode": _PLAN_MODE.get(sid, False),
            "ce_mode": _ce_mode_from_access(_ACCESS.get(sid, "local")),
            "research_mode": _RESEARCH_MODE.get(sid, False),
        }
        # Wrap stream_gpt in a resume loop. yolo self-wake: when the watchdog cuts a hang
        # and sets _self_wake_pending, put the partial result into history and add a
        # "continue" turn to run once more. A transient delay finishes; a real stuck cuts
        # again next round, exceeds SELF_WAKE_MAX, and ends with error.
        full_text = ""
        while True:
            _gpt_task = asyncio.ensure_future(stream_gpt(
                messages=history,
                system=system_prompt,
                on_token=on_token,
                on_tool_start=on_tool_start,
                on_tool_done=on_tool_done,
                on_consent=on_consent,
                on_waiting=on_waiting,
                on_reasoning=on_reasoning,
                images=images or None,
                working_dir=wdir,
                stats=usage_stats,
                plan_mode=_PLAN_MODE.get(sid, False),
                ce_mode=_ce_mode_from_access(_ACCESS.get(sid, "local")),
                research_mode=_RESEARCH_MODE.get(sid, False),
                load_override=_load_ov,
                spawn_context=parent_spawn_context,
            ))
            _wd = asyncio.ensure_future(_watchdog(_gpt_task))
            try:
                full_text = await _gpt_task
            except asyncio.CancelledError:
                # Was this a self-wake cancel? If so resume; otherwise (user abort etc.) propagate.
                if reg.pop("_self_wake_pending", None) is not None:
                    partial_so_far = _slim_assistant_content("".join(partial))
                    if partial_so_far.strip():
                        history.append({"role": "assistant", "content": partial_so_far})
                    history.append({"role": "user", "content":
                        "(시스템: 직전 응답이 무활동으로 자동 중단됐다. 중단 지점부터 "
                        "이어서 계속 진행해. 이미 끝난 작업을 반복하지 말고 남은 것만 마저 해.)"})
                    partial.clear()
                    _push_event(reg, {"event": "thinking", "data": {"label": "이어서 계속하는 중…"}})
                    continue
                raise
            finally:
                _wd.cancel()
            break
        # Slim version with tool narration stripped — into both next-turn history and DB text.
        # UI tool display is restored from events, so slimming text keeps the revisit screen intact.
        slim_text = _slim_assistant_content(full_text)
        history.append({"role": "assistant", "content": slim_text})
        # Cost calculation + model identification — must finish before DB save so it's persisted in usage_meta
        _llm_router.enrich_usage_stats(usage_stats)
        try:
            from pipeline.overthinking_telemetry import record_turn
            record_turn(sid, usage_stats)
        except Exception:
            pass
        if full_text:
            # 도구가 하나라도 쓰였으면 인터리빙 events 저장 → 재방문 시 시간순 복원.
            # 순수 텍스트 응답은 events 불필요(텍스트 폴백이 더 가벼움).
            has_tool = any(e.get("type") == "tool" for e in events)
            append_message(
                sid, "assistant", slim_text,
                usage_meta=dict(usage_stats) if usage_stats else None,
                events=events if has_tool else None,
            )
            # 첫 왕복 완료 시 제목 자동 생성 (백그라운드)
            session_meta = get_session(sid)
            if session_meta and session_meta.get("msg_count", 0) == 2:
                asyncio.create_task(_auto_title_session(sid))

        if _needs_compaction(history) and sid not in _COMPACTING:
            # 응답 경로에서 await하지 않는다 — 요약 LLM 호출(수십 초)이 done 이벤트를
            # 막아 "응답이 끝났는데 입력이 잠기는" 체감 끊김을 만들었다 (INT-1430).
            # 스냅샷을 요약하고, 완료 시 splice_compacted로 접합 — 압축 중 도착한
            # 새 메시지는 보존된다. 알림 배너는 다음 턴 시작 시 전송.
            _COMPACTING.add(sid)
            _compact_snapshot = list(history)
            # keep_recent는 memory_settings.json 핫리로드 — compact_history와 같은 값을 봐야
            # splice_compacted 접합 지점이 어긋나지 않는다.
            _n_summarized = max(0, len(_compact_snapshot) - _keep_recent())

            async def _bg_compact():
                try:
                    new_hist, summary = await compact_history(_compact_snapshot)
                    live = _SESSION_HISTORY.get(sid)
                    if live is not None:
                        splice_compacted(live, new_hist[0], _n_summarized)
                    _PENDING_COMPACT_NOTICE[sid] = summary
                except Exception:
                    logging.getLogger(__name__).warning(
                        "백그라운드 compaction 실패 (sid=%s) — 다음 턴에 재시도", sid, exc_info=True
                    )
                finally:
                    _COMPACTING.discard(sid)

            asyncio.create_task(_bg_compact())

        _push_event(reg, {"event": "done", "data": {"session_id": sid, "usage": usage_stats}})

    except asyncio.CancelledError:
        # 명시적 취소 (사용자가 /abort 등으로 요청한 경우만) — 부분 응답 보존.
        # 텍스트 토큰이 없어도 도구가 실행됐으면 그 흔적을 남겨야 재방문 시 사라지지 않는다.
        saved = _build_aborted_message("".join(partial), reg.get("_tool_trace", []))
        if saved:
            history.append({"role": "assistant", "content": saved})
            # 도구가 쓰였으면 events도 저장 — 중단 마커를 끝에 붙여 재방문 시 ⏹ 복원.
            has_tool = any(e.get("type") == "tool" for e in events)
            saved_events = (events + [{"type": "aborted"}]) if has_tool else None
            append_message(sid, "assistant", saved, events=saved_events)
        elif history and history[-1]["role"] == "user":
            history.pop()
        _push_event(reg, {"event": "error", "data": {"message": "응답이 취소됐어."}})
    except Exception as e:
        _push_event(reg, {"event": "error", "data": {"message": str(e)}})
    finally:
        reg["done"] = True
        reg["consumer"].set()  # wake consumer once more so it detects done
        # Remove from registry after buffer is consumed (prevent memory leak)
        async def _cleanup():
            # Retain for 10 min — allows buf replay if client reconnects
            await asyncio.sleep(600)
            _TASK_REGISTRY.pop(sid, None)
        asyncio.create_task(_cleanup())


def _persist_attached_image(img: dict) -> str:
    """첨부 이미지(base64)를 uploads/에 저장하고 호스트 경로를 반환 (INT-1457).

    프론트의 /api/upload/image 가 실패해 path 없이 도착한 첨부도
    image_generate(image_path=...) 편집이 가능하도록 서버에서 경로를 보장한다."""
    import base64 as _b64
    import uuid as _uuid
    ext_map = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
               "image/webp": "webp", "image/gif": "gif",
               "image/heic": "heic", "image/heif": "heif"}
    ext = ext_map.get((img.get("media_type") or "").lower(), "png")
    dest_dir = _uploads_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{_uuid.uuid4().hex[:8]}_attached.{ext}"
    # 크기 제한 (INT-2231) — 거대 base64 페이로드로 메모리/디스크 소진 방지.
    b64data = img.get("data") or ""
    if len(b64data) > 28 * 1024 * 1024:  # ~20MB raw 의 base64 상한
        raise ValueError("첨부 이미지가 너무 큽니다 (최대 20MB)")
    raw = _b64.b64decode(b64data)
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("첨부 이미지가 너무 큽니다 (최대 20MB)")
    dest.write_bytes(raw)
    return str(dest)


@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    data = await request.json()
    user_text = data.get("message", "").strip()
    images: list[dict] = data.get("images", [])
    requested_sid = data.get("session_id")
    reattach = bool(data.get("reattach", False))

    # Reattach mode: reconnect to the in-progress task SSE buffer without a new message.
    # Empty message handling comes after the reattach branch — no message is expected on reattach.
    if not user_text and not images and not reattach:
        return JSONResponse({"error": "empty message"}, status_code=400)
    if reattach:
        # Reattach requires an existing session
        if not requested_sid or requested_sid not in _TASK_REGISTRY:
            return JSONResponse({"error": "no active task for session"}, status_code=404)
        sid = requested_sid
    else:
        if not user_text:
            user_text = "(이미지 첨부됨)"
        sid = get_or_create_session(requested_sid)

    # Refresh access level on each request (reflects enterprise key presence on reattach)
    _ACCESS[sid] = _get_access_level(request)

    # Original text for display (stored in DB/UI). Commands are expanded before sending to GPT.
    display_text = user_text

    # Handle slash commands first
    if user_text.startswith("/"):
        result = handle_slash(user_text, sid)
        if result is not None:
            # Built-in command — respond with text immediately
            text = result["text"]
            target_sid = result.get("switch_session", sid)
            if target_sid != sid:
                sid = target_sid

            async def slash_gen():
                yield f"event: token\ndata: {json.dumps({'token': text}, ensure_ascii=False)}\n\n"
                yield f"event: done\ndata: {json.dumps({'session_id': sid}, ensure_ascii=False)}\n\n"

            return StreamingResponse(
                slash_gen(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # Not a built-in → try expanding as custom command (data/commands/*.md)
        from pipeline.commands import get_command, expand_command
        _parts = user_text.split(None, 1)
        _cname = _parts[0][1:].lower()  # strip leading /
        _cargs = _parts[1].strip() if len(_parts) > 1 else ""
        _cmd = get_command(_cname)
        if _cmd is not None:
            if _cname == "goal":
                # /goal is multi-turn long-running mode. Resending the protocol (604 tokens)
                # as a user message every turn wastes tokens + bloats history → instead just
                # enable the session mode and inject the protocol into system_prompt once
                # (_GOAL_MODE_GUIDE, see _run_gpt_task). Only the user's actual goal goes to GPT.
                # The hang watchdog threshold also extends to 300s.
                _GOAL_MODE[sid] = True
                user_text = _cargs or user_text
                display_text = user_text
            else:
                # Pass expanded instruction to GPT (GPT path below processes user_text)
                user_text = expand_command(_cmd, _cargs)
                # Keep display_text as original (/commit ...) — stored cleanly in UI/DB

    last_event_id: int = data.get("last_event_id", 0)

    # If a task for this session is already running → attach to existing stream (reconnect)
    if sid in _TASK_REGISTRY and not _TASK_REGISTRY[sid]["done"]:
        reg = _TASK_REGISTRY[sid]
        # Reconnect: start from beginning if last_event_id==0, else resume from that index
        start_cursor = last_event_id if last_event_id > 0 else 0
    elif sid in _TASK_REGISTRY and _TASK_REGISTRY[sid]["done"] and last_event_id > 0:
        # Task done but client missed the done event — retransmit buf
        reg = _TASK_REGISTRY[sid]
        start_cursor = last_event_id
    elif reattach:
        # Reattach mode, done, last_event_id==0 → retransmit from start
        # (previous elif only catches last_event_id>0, so this handles the rest)
        reg = _TASK_REGISTRY[sid]
        start_cursor = 0
    else:
        # New request — append to history then create task
        # display_text: stored in UI/DB + used for DB search (original /commit if command)
        # user_text: content sent to GPT (expanded instruction if command)
        loop = asyncio.get_event_loop()
        keyword_hits = await loop.run_in_executor(None, search_events, display_text[:500], 5)
        augmented = user_text + _format_db_context(keyword_hits)
        # Image path hint — used by image_generate(image_path=...) for editing.
        # path 없는 첨부(프론트 업로드 실패/생략)는 서버가 직접 저장해 경로를 보장한다 —
        # 경로 힌트가 없으면 편집 요청이 이미지 도구로 연결되지 못한다 (INT-1457).
        if images:
            paths = []
            for img in images:
                p = img.get("path") or ""
                if not p and img.get("data"):
                    try:
                        p = _persist_attached_image(img)
                        img["path"] = p
                    except Exception:
                        p = ""
                if p:
                    paths.append(p)
            if paths:
                path_hints = "\n".join(f"[첨부 이미지 경로] {p}" for p in paths)
                augmented = augmented + f"\n\n{path_hints}"
        history = _get_history(sid)
        history.append({"role": "user", "content": augmented})
        await loop.run_in_executor(None, append_message, sid, "human", display_text)
        _heartbeat_resumes.pop(sid, None)  # user sent a new message → reset heartbeat resume count

        reg = {
            "task": None,
            "buf": [],
            "done": False,
            "consumer": asyncio.Event(),
            "approval_queue": asyncio.Queue(),
            "last_activity": time.monotonic(),
            "awaiting_approval": False,  # watchdog exempt while awaiting approval
        }
        _TASK_REGISTRY[sid] = reg
        reg["task"] = asyncio.create_task(_run_gpt_task(sid, history, images))
        start_cursor = 0

    # SSE generator — drains buf. Task continues even if connection drops.
    cursor = [start_cursor]

    async def event_gen_full():
        try:
            while True:
                try:
                    await asyncio.wait_for(reg["consumer"].wait(), timeout=5.0)
                    reg["consumer"].clear()
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                while cursor[0] < len(reg["buf"]):
                    item = reg["buf"][cursor[0]]
                    idx = cursor[0]
                    cursor[0] += 1
                    # id field: client can track with Last-Event-ID
                    yield (
                        f"id: {idx}\n"
                        f"event: {item['event']}\n"
                        f"data: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                    )

                if reg["done"] and cursor[0] >= len(reg["buf"]):
                    break
        except GeneratorExit:
            # Connection dropped — do not touch the task
            return

    return StreamingResponse(
        event_gen_full(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── WebSocket PTY terminal ────────────────────────────────────────────────────

@app.websocket("/ws/terminal/{sid}")
async def terminal_ws(websocket: WebSocket, sid: str):
    import subprocess
    await websocket.accept()
    # CSWSH 방어 (INT-2231): WebSocket 은 CORS preflight 대상이 아니므로, 브라우저가 보낸
    # Origin 이 화이트리스트에 없으면 거부한다. IP 게이트만으로는 같은 머신의 악성 웹페이지가
    # ws://127.0.0.1:8100/ws/terminal 로 cross-site 연결(loopback peer → IP 게이트 통과)하는
    # 것을 못 막는다. Origin 이 없는 경우(native/CLI 클라이언트)는 아래 IP 게이트로만 판정.
    _origin = websocket.headers.get("origin")
    if _origin and _origin not in _ALLOWED_ORIGINS:
        await websocket.close(code=1008)
        return
    # HTTP 미들웨어는 WebSocket scope를 커버하지 않으므로 여기서 직접 원격 게이트.
    # loopback 또는 허용된 원격(Tailscale/enterprise key)이 아니면 즉시 닫는다.
    if not _state_mod.is_remote_allowed(websocket):
        await websocket.send_text(
            "\r\n[VEGA] 원격 접속에서 내장 터미널은 허용되지 않습니다.\r\n"
        )
        await websocket.close(code=1008)
        return
    if not _HAS_PTY:
        # Windows: pty/fcntl/termios 부재 — 내장 터미널만 미지원, 즉시 정상 종료.
        await websocket.send_text("\r\n[VEGA] 이 플랫폼에선 내장 터미널을 아직 지원하지 않습니다.\r\n")
        await websocket.close()
        return
    loop = asyncio.get_event_loop()

    cwd = get_working_dir(sid) or str(Path.home())
    shell = os.environ.get("SHELL", "/bin/zsh")
    env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"}

    master_fd, slave_fd = pty.openpty()

    def _set_size(cols: int, rows: int) -> None:
        buf = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, buf)
        except OSError:
            pass

    _set_size(220, 50)

    def _preexec():
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        [shell, "-l"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        preexec_fn=_preexec,
        cwd=cwd,
        env=env,
        close_fds=True,
    )
    os.close(slave_fd)

    async def _pty_to_ws():
        try:
            while True:
                data = await loop.run_in_executor(None, lambda: os.read(master_fd, 4096))
                if not data:
                    break
                await websocket.send_bytes(data)
        except (OSError, WebSocketDisconnect):
            pass

    reader_task = asyncio.ensure_future(_pty_to_ws())

    try:
        while True:
            msg = await websocket.receive()
            if "bytes" in msg:
                raw = msg["bytes"]
            elif "text" in msg:
                raw = msg["text"]
                if isinstance(raw, str):
                    try:
                        ctrl = json.loads(raw)
                        if ctrl.get("type") == "resize":
                            _set_size(ctrl.get("cols", 80), ctrl.get("rows", 24))
                            continue
                    except json.JSONDecodeError:
                        pass
                    raw = raw.encode()
            else:
                continue
            await loop.run_in_executor(None, lambda d=raw: os.write(master_fd, d if isinstance(d, bytes) else d.encode()))
    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()
        try:
            proc.terminate()
            await loop.run_in_executor(None, proc.wait)
        except OSError:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
