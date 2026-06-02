# Created: 2026-05-18
# Purpose: VEGA FastAPI server — replaces Chainlit
# Dependencies: fastapi, uvicorn, pipeline/streaming.py, pipeline/session_store.py
# Test Status: under validation

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import struct
import sys
import termios
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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
from pipeline.tools import TOOL_SCHEMAS, patch_account_enum
from pipeline.contact_store import startup_sync
from pipeline.compaction import compact_history, _needs_compaction

STATIC_DIR = Path(__file__).parent / "static"

from pipeline.data_paths import charts_dir as _charts_dir, uploads_dir as _uploads_dir
CHART_DIR = _charts_dir()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sync contacts DB
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, startup_sync)
        print(f"[Contacts] iCloud sync done: {result['synced']} updated, {result['total']} total")
    except Exception as e:
        print(f"[Contacts] sync warning: {e}")

    # Collect MCP tool list → merge into TOOL_SCHEMAS
    try:
        mcp_schemas = await init_mcp_tools()
        for server_name, schemas in mcp_schemas.items():
            TOOL_SCHEMAS.extend(schemas)
            print(f"[MCP] {server_name}: {len(schemas)} tools registered")
    except Exception as e:
        print(f"[MCP] init warning: {e}")

    # Update Gmail/Calendar/Drive account enum from user_profile account list
    try:
        patch_account_enum()
    except Exception as e:
        print(f"[Profile] account enum patch warning: {e}")

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

    # Docker 코드 샌드박스 자동 확보 (백그라운드) — 기동 시 컨테이너를 띄워두어
    # 첫 bash_exec/python_exec 호출 시 빌드/기동 지연을 없앤다. Docker 없으면 조용히 skip.
    async def _warmup_sandbox():
        try:
            from pipeline.sandbox import ensure_sandbox_ready
            result = await asyncio.get_event_loop().run_in_executor(None, ensure_sandbox_ready)
            if result.get("ready"):
                print(f"[Sandbox] ready ({result.get('reason')})")
            else:
                print(f"[Sandbox] skipped ({result.get('reason')}) — 코드 실행 도구는 Docker 기동 후 사용 가능")
        except Exception as e:
            print(f"[Sandbox] warmup warning: {e}")

    asyncio.create_task(_warmup_sandbox())

    # Pre-create heartbeat / ops layer tables (prevents DDL write lock during runtime)
    try:
        from pipeline.heartbeat import (
            _ensure_briefs_table,
            _ensure_suggest_cache_table,
            _ensure_table,
        )
        _ensure_table()
        _ensure_suggest_cache_table()
        _ensure_briefs_table()
    except Exception as e:
        print(f"[Heartbeat] table init warning: {e}")

    try:
        from pipeline.project_state import _ensure_project_state_table, seed_project_states
        _ensure_project_state_table()
        seed_project_states()
    except Exception as e:
        print(f"[ProjectState] table init warning: {e}")

    yield


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
app.include_router(_llm_router.router)
app.include_router(_fs_router.router)
app.include_router(_dashboard_router.router)
app.include_router(_widgets_router.router)
app.include_router(_onboarding_router.router)
app.include_router(_run_log_router.router)
app.include_router(_scheduler_router.router)
app.include_router(_memory_inspector_router.router)

# CORS — allow Tauri app origin + localhost only. Wildcard removed to block cross-origin CSRF.
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "tauri://localhost",
        "http://localhost:8100",
        "http://127.0.0.1:8100",
        "http://localhost:8101",   # dev hot-reload port
        "http://127.0.0.1:8101",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/api/charts", StaticFiles(directory=str(CHART_DIR)), name="charts")

# In-memory session history cache — restored from DB on restart
_SESSION_HISTORY: dict[str, list[dict]] = {}

# /plan mode toggle — sid → bool. When True, build_system injects the plan guide
# and the dispatch layer blocks write/exec tools. Volatile: cleared on server restart.
_PLAN_MODE: dict[str, bool] = {}

# /research mode toggle — sid → bool.
# When True, injects the research guide (hypothesis→evidence→conclusion, web-search-first,
# cite sources) into the system prompt and raises stream_gpt max_rounds to 40.
# Volatile: cleared on session end.
_RESEARCH_MODE: dict[str, bool] = {}

# /yolo mode toggle — sid → bool. When True, auto-reruns host_exec with ask="off"
# immediately upon receiving a __needs_approval__ response.
# Hard-block list (_HOST_HARD_BLOCKED) and secret checks apply regardless of yolo.
# AskUserQuestion, exit_plan_mode, improvement_pending still require user approval.
# Volatile: cleared on session end.
_YOLO_MODE: dict[str, bool] = {}

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
# "ce"         : remote connection without key — local system tools excluded
_ACCESS: dict[str, str] = {}

_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "0.0.0.0"}
_ENT_KEY_KC = "vega-enterprise-keys"   # Keychain service name


def _load_enterprise_keys() -> frozenset[str]:
    """Load enterprise key list from Keychain. Comma-separated string."""
    try:
        from pipeline.keychain import get_secret
        raw = get_secret(_ENT_KEY_KC, service=_ENT_KEY_KC) or ""
        return frozenset(k.strip() for k in raw.split(",") if k.strip())
    except Exception:
        return frozenset()


def _is_loopback(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded in _LOOPBACK or forwarded.startswith("127.")
    host = request.client.host if request.client else "127.0.0.1"
    return host in _LOOPBACK or host.startswith("127.") or host.startswith("::ffff:127.")


def _get_access_level(request: Request) -> str:
    """요청의 접근 레벨 반환: 'local' | 'enterprise'

    CE 모드 제거(2026-06-02): 비-loopback·키 없는 원격 접속도 더 이상 'ce'로
    강등하지 않는다. 모든 세션이 로컬 시스템 도구 전체를 받는다.
    """
    if _is_loopback(request):
        return "local"
    key = request.headers.get("x-vega-key", "").strip()
    if key and key in _load_enterprise_keys():
        return "enterprise"
    # 과거엔 "ce"(로컬 도구 차단)였으나 CE 모드 폐지로 local과 동일 취급.
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

# ── Task registry — tasks run to completion regardless of connection state ─────────────────────
# key: session_id
# value: {
#   "task": asyncio.Task,          # run_gpt task
#   "buf":  list[dict],            # unconsumed event buffer
#   "done": bool,                  # task fully finished
#   "consumer": asyncio.Event,     # signals new event arrival
# }
_TASK_REGISTRY: dict[str, dict] = {}


def _get_history(sid: str) -> list[dict]:
    if sid not in _SESSION_HISTORY:
        _SESSION_HISTORY[sid] = load_history(sid)
    return _SESSION_HISTORY[sid]


# ── Tool status labels ────────────────────────────────────────────────────────

def _tool_label(name: str, args: dict) -> str:
    query = args.get("query", "")
    cmd = args.get("command", "")[:80]
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
        "host_exec":             f"🖥️  호스트 실행 중: `{cmd}`",
        "bash_exec":             f"⚙️  실행 중: `{cmd}`",
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
        return ("✓ 완료", None)

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

    if isinstance(parsed, list):
        return (f"✓ {len(parsed)}건", None)

    return ("✓ 완료", None)


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
        _YOLO_MODE[sid] = True
        return {"text": (
            "⚡ **YOLO 모드 켜짐.** 이제 host_exec의 allowlist 외 명령도 사용자 승인 없이 "
            "자동 실행한다. 단, 하드 차단(rm -rf /, mkfs, > /dev/ 등)과 시크릿 검사는 그대로 적용.\n\n"
            "AskUserQuestion(선택지), exit_plan_mode, self_improve 패치는 여전히 사용자 승인 필요.\n\n"
            "`/yolo-off`로 해제."
        )}

    if cmd == "/yolo-off":
        was = _YOLO_MODE.pop(sid, False)
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



_UPLOAD_DIR = _uploads_dir()


@app.post("/api/upload")
async def upload_file(request: Request):
    """
    Drag-and-drop file upload — saves file to data/uploads/ and returns the path.
    Encrypted Office files are decrypted before saving.
    Returns: {"filename": str, "path": str} | {"error": "password_required"} (401)
    """
    import io, uuid as _uuid

    MAX_SIZE = 100 * 1024 * 1024  # 100 MB limit

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        return JSONResponse({"error": "file 필드가 없습니다"}, status_code=400)

    raw = await upload.read()
    if len(raw) > MAX_SIZE:
        return JSONResponse({"error": f"파일이 너무 큽니다 ({len(raw)//1024//1024}MB)"}, status_code=413)

    fname = upload.filename or "unknown"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    password = (form.get("password") or "").strip() or None

    # Decrypt encrypted Office files
    if ext in ("xlsx", "xlsm", "xltx", "xls", "xlsb", "ods", "docx", "doc", "pptx", "ppt"):
        try:
            import msoffcrypto
            of = msoffcrypto.OfficeFile(io.BytesIO(raw))
            if of.is_encrypted():
                if not password:
                    return JSONResponse({"error": "password_required"}, status_code=401)
                try:
                    out = io.BytesIO()
                    of.load_key(password=password)
                    of.decrypt(out)
                    raw = out.getvalue()
                except Exception:
                    return JSONResponse({"error": "비밀번호가 틀렸습니다"}, status_code=403)
        except Exception:
            pass  # files that msoffcrypto cannot parse are saved as-is

    # Add uuid prefix to avoid filename collisions
    safe_name = f"{_uuid.uuid4().hex[:8]}_{fname}"
    dest = _UPLOAD_DIR / safe_name
    dest.write_bytes(raw)

    return JSONResponse({"filename": fname, "path": str(dest)})


@app.post("/api/upload/image")
async def upload_image_base64(request: Request):
    """Save base64 image to data/uploads/ and return the path.
    Called alongside adding images to pendingImages in the UI to secure an editable path.
    body: {data: "base64...", media_type: "image/png", name: "filename.png"}
    Returns: {"path": str, "filename": str}
    """
    import base64 as _b64
    import uuid as _uuid

    body = await request.json()
    data_str = body.get("data", "")
    media_type = body.get("media_type", "image/png")
    name = body.get("name", "image.png")

    if not data_str:
        return JSONResponse({"error": "data 필드가 없습니다"}, status_code=400)

    ext_map = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
               "image/webp": "webp", "image/gif": "gif"}
    ext = ext_map.get(media_type, "png")
    safe_name = f"{_uuid.uuid4().hex[:8]}_{name}"
    if not safe_name.endswith(f".{ext}"):
        safe_name = safe_name.rsplit(".", 1)[0] + f".{ext}"

    dest = _UPLOAD_DIR / safe_name
    dest.write_bytes(_b64.b64decode(data_str))
    return JSONResponse({"path": str(dest), "filename": safe_name})


@app.post("/api/stt")
async def stt_transcribe(request: Request):
    """
    Speech-to-Text transcription endpoint.
    Accepts multipart/form-data with a 'file' field (audio: webm/mp4/wav/ogg/mp3/flac).
    Optional 'language' field overrides the config language (e.g. 'ko', 'en').
    Returns: {"text": str}
    """
    MAX_AUDIO_SIZE = 25 * 1024 * 1024  # 25 MB (Whisper API limit)

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        return JSONResponse({"error": "file 필드가 없습니다"}, status_code=400)

    raw = await upload.read()
    if len(raw) > MAX_AUDIO_SIZE:
        return JSONResponse({"error": f"오디오 파일이 너무 큽니다 ({len(raw)//1024//1024}MB, 최대 25MB)"}, status_code=413)

    filename = getattr(upload, "filename", None) or "audio.webm"
    language_override = (form.get("language") or "").strip() or None

    try:
        from pipeline.stt_gateway import transcribe as _transcribe, LocalSTTUnavailable
        text = _transcribe(raw, filename=filename, language_override=language_override)
        return JSONResponse({"text": text})
    except LocalSTTUnavailable as e:
        return JSONResponse({"error": str(e), "code": "local_stt_unavailable"}, status_code=503)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": f"STT 처리 실패: {e}"}, status_code=500)


@app.get("/api/stt/config")
async def stt_get_config():
    """Returns current STT configuration."""
    from pipeline.stt_gateway import get_stt_config
    return JSONResponse(get_stt_config())


@app.post("/api/stt/config")
async def stt_set_config(request: Request):
    """Updates STT configuration. Body: {provider, model, language, response_format, endpoint?}"""
    from pipeline.stt_gateway import set_stt_config
    body = await request.json()
    allowed = {"provider", "model", "language", "response_format", "endpoint", "api_key_env"}
    cleaned = {k: v for k, v in body.items() if k in allowed}
    set_stt_config(cleaned)
    return JSONResponse({"ok": True})


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

    # 샌드박스(Docker) 가용성 — 꺼져 있으면 bash_exec/python_exec/sandbox 가
    # 등록돼 있어도 실제 실행이 안 된다. "도구가 몇 개 안 보인다"의 흔한 원인이라
    # health 에 노출해 진단을 쉽게 한다.
    sandbox_status = "unknown"
    try:
        from pipeline.sandbox import docker_available, _container_running
        if not docker_available():
            sandbox_status = "docker_off"   # Docker Desktop 미기동/미설치
        elif _container_running():
            sandbox_status = "ok"
        else:
            sandbox_status = "container_down"  # Docker 는 떠 있으나 컨테이너 미기동
    except Exception:
        sandbox_status = "unknown"

    # 전체 도구 개수(office/sandbox 포함) — TOOL_SCHEMAS 기준.
    try:
        from pipeline.tools import TOOL_SCHEMAS
        total_tools = len(TOOL_SCHEMAS)
    except Exception:
        total_tools = 0

    return JSONResponse({
        "status": "ok",
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

    # host_exec approval — execute, then push result to session approval_queue → resumes VEGA loop
    command = body.get("command", "")

    if not approved:
        # Rejected: push rejection signal to queue
        reg = _TASK_REGISTRY.get(sid)
        if reg and "approval_queue" in reg:
            await reg["approval_queue"].put({"approved": False, "result": None})
        return JSONResponse({"ok": False, "result": "거절됨"})

    try:
        from pipeline.tools_code import host_exec
        exec_result = host_exec(command, ask="off")
    except Exception as e:
        exec_result = {"error": str(e)}

    reg = _TASK_REGISTRY.get(sid)
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


@app.get("/api/sessions")
async def sessions_list(include_archived: int = 0, limit: int = 30):
    sessions = list_sessions(limit=limit, include_archived=bool(include_archived))
    return JSONResponse({"sessions": sessions})


@app.post("/api/sessions/{sid}/archive")
async def session_archive(sid: str, request: Request):
    """Toggle session archived flag. body: {archived: bool}. Defaults to True if missing."""
    from pipeline.session_store import set_archived
    try:
        data = await request.json()
    except Exception:
        data = {}
    archived = bool(data.get("archived", True))
    ok = set_archived(sid, archived)
    if not ok:
        return JSONResponse({"error": "세션 없음"}, status_code=404)
    # Archiving is just a flag; in-progress tasks continue and SSE remains unaffected
    return JSONResponse({"ok": True, "archived": archived})


@app.post("/api/sessions/{sid}/workdir")
async def set_session_workdir(sid: str, request: Request):
    """Set or clear session working directory. body: {path: str|null}"""
    data = await request.json()
    path = data.get("path")
    if path:
        p = Path(path).expanduser()
        if not p.is_dir():
            return JSONResponse({"error": f"폴더가 존재하지 않음: {path}"}, status_code=400)
        path = str(p)
    set_working_dir(sid, path)
    return JSONResponse({"ok": True, "working_dir": path})


@app.get("/api/sessions/{sid}/workdir")
async def get_session_workdir(sid: str):
    return JSONResponse({"working_dir": get_working_dir(sid)})


@app.get("/api/sessions/{sid}/plan-mode")
async def get_plan_mode(sid: str):
    """Query current session plan mode state — for header badge update."""
    return JSONResponse({"plan_mode": bool(_PLAN_MODE.get(sid, False))})


@app.get("/api/sessions/{sid}/research-mode")
async def get_research_mode(sid: str):
    """Query current session research mode state — for header badge update."""
    return JSONResponse({"research_mode": bool(_RESEARCH_MODE.get(sid, False))})


@app.get("/api/sessions/{sid}/yolo-mode")
async def get_yolo_mode(sid: str):
    """Query current session YOLO mode state — for header badge update."""
    return JSONResponse({"yolo_mode": bool(_YOLO_MODE.get(sid, False))})


# Grace timeout for approval wait (seconds). Sessions exceeding this without response are treated as zombies.
_AWAITING_GRACE_SEC = 30 * 60  # 30 minutes


@app.get("/api/sessions/active")
async def get_active_sessions():
    """List in-progress GPT tasks — for UI status bar spinner and reconnect decision on session switch.
    Sessions exceeding _AWAITING_GRACE_SEC since awaiting_since are auto-aborted and excluded.
    Returns: {"active": [{"sid": str, "awaiting_approval": bool, "awaiting_age_sec": int|null, "last_event_id": int}, ...]}
    """
    now = time.time()
    zombies: list[str] = []
    active: list[dict] = []
    for sid, reg in _TASK_REGISTRY.items():
        if reg.get("done"):
            continue
        awaiting = bool(reg.get("awaiting_approval", False))
        awaiting_since = reg.get("awaiting_since")
        age = int(now - awaiting_since) if awaiting_since else None
        # Auto-clean zombie sessions that exceeded the grace period
        if awaiting and age is not None and age > _AWAITING_GRACE_SEC:
            zombies.append(sid)
            continue
        active.append({
            "sid": sid,
            "awaiting_approval": awaiting,
            "awaiting_age_sec": age,
            "last_event_id": len(reg.get("buf", [])),
        })
    # Clean up zombies via the same abort path — push reject signal to approval_queue + cancel task
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
        active_tools = filter_tools(_tools.TOOL_SCHEMAS)
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




# Built-in slash commands (metadata for autocomplete)

@app.get("/api/sessions/{sid}/history")
async def session_history(sid: str):
    session = get_session(sid)
    if not session:
        return JSONResponse({"error": "not found"}, status_code=404)
    from pipeline.session_store import load_history_with_meta
    messages = load_history_with_meta(sid)
    return JSONResponse({"uuid": sid, "name": session["name"], "messages": messages})


@app.post("/api/sessions")
async def session_create(request: Request):
    data = await request.json()
    title = data.get("title", "VEGA 세션")
    sid = create_session(title)
    _SESSION_HISTORY[sid] = []
    return JSONResponse({"uuid": sid, "name": title})


@app.put("/api/sessions/{sid}/rename")
async def session_rename(sid: str, request: Request):
    data = await request.json()
    name = data.get("name", "VEGA 세션")
    rename_session(sid, name)
    return JSONResponse({"ok": True})


@app.delete("/api/sessions/{sid}")
async def session_delete(sid: str):
    delete_session(sid)
    _SESSION_HISTORY.pop(sid, None)
    _PLAN_MODE.pop(sid, None)
    _ACCESS.pop(sid, None)
    return JSONResponse({"ok": True})


# ── Enterprise key management (local access only) ────────────────────────────

def _require_local(request: Request):
    if not _is_loopback(request):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Only local connections are allowed.")

@app.get("/api/admin/keys")
async def admin_keys_list(request: Request):
    _require_local(request)
    keys = sorted(_load_enterprise_keys())
    return JSONResponse({"keys": keys, "count": len(keys)})

@app.post("/api/admin/keys")
async def admin_keys_add(request: Request):
    _require_local(request)
    body = await request.json()
    key = (body.get("key") or "").strip()
    if not key:
        return JSONResponse({"error": "key 필드 필요"}, status_code=400)
    if not key.startswith("vk_"):
        return JSONResponse({"error": "키는 vk_ 로 시작해야 합니다"}, status_code=400)
    from pipeline.keychain import get_secret, set_secret
    existing = set(k.strip() for k in (get_secret(_ENT_KEY_KC, service=_ENT_KEY_KC) or "").split(",") if k.strip())
    existing.add(key)
    set_secret(_ENT_KEY_KC, ",".join(sorted(existing)), service=_ENT_KEY_KC)
    return JSONResponse({"ok": True, "key": key, "total": len(existing)})

@app.delete("/api/admin/keys/{key}")
async def admin_keys_delete(key: str, request: Request):
    _require_local(request)
    from pipeline.keychain import get_secret, set_secret
    existing = set(k.strip() for k in (get_secret(_ENT_KEY_KC, service=_ENT_KEY_KC) or "").split(",") if k.strip())
    existing.discard(key)
    set_secret(_ENT_KEY_KC, ",".join(sorted(existing)), service=_ENT_KEY_KC)
    return JSONResponse({"ok": True, "remaining": len(existing)})


# ── Core SSE streaming endpoint ───────────────────────────────────────────────

def _push_event(reg: dict, event: dict) -> None:
    """Append event to the task registry and wake the consumer."""
    reg["buf"].append(event)
    reg["last_activity"] = time.monotonic()  # for watchdog — detect inactivity hang
    reg["consumer"].set()


async def _run_gpt_task(sid: str, history: list[dict], images: list[dict]) -> None:
    """
    Background GPT task fully decoupled from the connection.
    Appends events to _TASK_REGISTRY[sid]["buf"] and signals via consumer Event.
    Task runs to completion even if the client disconnects.
    """
    reg = _TASK_REGISTRY[sid]
    partial: list[str] = []

    async def on_waiting():
        _push_event(reg, {"event": "thinking", "data": {"label": "생각 중…"}})

    async def on_token(tok: str):
        partial.append(tok)
        _push_event(reg, {"event": "token", "data": {"token": tok}})

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
        # Register real-time output streaming callback for host_exec
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
            row_id = record_start(session_id, name, args, call_id)
            reg.setdefault("_run_log", {})[call_id] = (row_id, _t.monotonic())
        except Exception:
            pass

    async def on_tool_done(name: str, result: str, call_id: str = "") -> str | None:
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("__needs_approval__"):
                command = parsed.get("command", "")
                # YOLO mode: skip approval wait and re-run immediately with ask="off".
                # Hard-block and secret checks apply inside host_exec regardless of ask mode — safe.
                if _YOLO_MODE.get(sid):
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
    WATCHDOG_IDLE = 180.0  # seconds

    async def _watchdog(target_task: asyncio.Task):
        while not target_task.done():
            await asyncio.sleep(15)
            if reg.get("awaiting_approval"):
                continue
            idle = time.monotonic() - reg.get("last_activity", time.monotonic())
            if idle > WATCHDOG_IDLE and not target_task.done():
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
        usage_stats: dict = {}
        _gpt_task = asyncio.ensure_future(stream_gpt(
            messages=history,
            system=system_prompt,
            on_token=on_token,
            on_tool_start=on_tool_start,
            on_tool_done=on_tool_done,
            on_waiting=on_waiting,
            images=images or None,
            working_dir=wdir,
            stats=usage_stats,
            plan_mode=_PLAN_MODE.get(sid, False),
            ce_mode=_ce_mode_from_access(_ACCESS.get(sid, "local")),
            research_mode=_RESEARCH_MODE.get(sid, False),
        ))
        _wd = asyncio.ensure_future(_watchdog(_gpt_task))
        try:
            full_text = await _gpt_task
        finally:
            _wd.cancel()
        history.append({"role": "assistant", "content": full_text})
        # Cost calculation + model identification — must finish before DB save so it's persisted in usage_meta
        _llm_router.enrich_usage_stats(usage_stats)
        if full_text:
            append_message(sid, "assistant", full_text, usage_meta=dict(usage_stats) if usage_stats else None)
            # Auto-generate title on first round-trip completion (background)
            session_meta = get_session(sid)
            if session_meta and session_meta.get("msg_count", 0) == 2:
                asyncio.create_task(_auto_title_session(sid))

        if _needs_compaction(history):
            async def on_compact_status(msg: str):
                _push_event(reg, {"event": "compacted", "data": {"status": msg}})
            new_history, summary = await compact_history(history, on_compact_status)
            history.clear()
            history.extend(new_history)
            _SESSION_HISTORY[sid] = history
            _push_event(reg, {"event": "compacted", "data": {"status": "done", "summary": summary}})

        _push_event(reg, {"event": "done", "data": {"session_id": sid, "usage": usage_stats}})

    except asyncio.CancelledError:
        # 명시적 취소 (사용자가 /abort 등으로 요청한 경우만) — 부분 응답 보존.
        # 텍스트 토큰이 없어도 도구가 실행됐으면 그 흔적을 남겨야 재방문 시 사라지지 않는다.
        saved = _build_aborted_message("".join(partial), reg.get("_tool_trace", []))
        if saved:
            history.append({"role": "assistant", "content": saved})
            append_message(sid, "assistant", saved)
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
        keyword_hits = search_events(display_text[:500], limit=5)
        augmented = user_text + _format_db_context(keyword_hits)
        # Image path hint — used by image_generate(image_path=...) for editing
        if images:
            paths = [img["path"] for img in images if img.get("path")]
            if paths:
                path_hints = "\n".join(f"[첨부 이미지 경로] {p}" for p in paths)
                augmented = augmented + f"\n\n{path_hints}"
        history = _get_history(sid)
        history.append({"role": "user", "content": augmented})
        append_message(sid, "human", display_text)

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
