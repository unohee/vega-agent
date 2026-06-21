# Created: 2026-05-18
# Purpose: VEGA GPT tool-use streaming loop — no Chainlit dependency
# Dependencies: pipeline/tools.py, pipeline/vega_query.py, pipeline/auth/chatgpt.py
# Test Status: under validation

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.ssl_ctx import certified_context

logger = logging.getLogger(__name__)

# Max chars for dashboard section in system prompt (guards token budget)
_DASHBOARD_MAX_CHARS = 3000
_COLLECT_TIMEOUT = 5.0  # max wait time per collector function (seconds)
_SSE_CONNECT_TIMEOUT = float(os.getenv("VEGA_SSE_CONNECT_TIMEOUT", "30"))
_SSE_IDLE_TIMEOUT = float(os.getenv("VEGA_SSE_IDLE_TIMEOUT", "90"))

from pipeline.auth.chatgpt import (
    CODEX_BASE_URL,
    DEFAULT_CODEX_MODEL,
    _load_profile,
    ensure_valid_token,
)
from pipeline.tools import TOOL_SCHEMAS, dispatch_tool, get_schemas_for_mode
from pipeline.vega_query import get_persona

_PERSONA_CACHE: str | None = None
_DASHBOARD_CACHE: tuple[float, str] | None = None  # (timestamp, text)
_DASHBOARD_TTL = 1800  # 30 minutes
_STATE_CACHE: tuple[float, str] | None = None  # (timestamp, text)
_STATE_TTL = 1800  # 30 minutes


def _collect_safe(fn, *args, timeout: float = _COLLECT_TIMEOUT, default=None, **kwargs):
    """Run a collector function in a separate thread within the timeout. Returns default on failure or timeout."""
    ex = ThreadPoolExecutor(max_workers=1)
    future = ex.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeoutError:
        future.cancel()
        logger.warning("context_collect timeout: %s (%.1fs)", fn.__name__, timeout)
        return default if default is not None else ([] if "list" in fn.__name__ else {})
    except Exception as e:
        logger.warning("context_collect error: %s — %s", fn.__name__, e)
        return default if default is not None else ([] if "list" in fn.__name__ else {})
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _build_dashboard_context() -> str:
    """Collect this week's calendar, Linear in-progress issues, and priority mail as text.
    Runs the three collectors in parallel via ThreadPoolExecutor to minimize total wait time.
    """
    from datetime import datetime as _dt

    from pipeline.context_collect import (
        collect_calendar,
        collect_linear_in_progress,
        collect_priority_mail_since,
    )

    kst = ZoneInfo("Asia/Seoul")
    today_str = _dt.now(kst).strftime("%Y-%m-%d")

    # Parallel collection — total wait = _COLLECT_TIMEOUT cap instead of sum of each function.
    # Do not use ThreadPoolExecutor as a context manager here: __exit__ waits for timed-out
    # collectors and can make the LLM appear frozen before the first token.
    ex = ThreadPoolExecutor(max_workers=3)
    futures = []
    try:
        f_cal = ex.submit(collect_calendar, 7)
        f_linear = ex.submit(collect_linear_in_progress, 8)
        f_mail = ex.submit(collect_priority_mail_since, 24)
        futures = [f_cal, f_linear, f_mail]

        def _get(future, default):
            try:
                return future.result(timeout=_COLLECT_TIMEOUT)
            except Exception:
                return default

        events_by_day: dict = _get(f_cal, {})
        issues: list = _get(f_linear, [])
        mails: list = _get(f_mail, [])
    finally:
        for f in futures:
            f.cancel()
        ex.shutdown(wait=False, cancel_futures=True)

    lines: list[str] = []

    # This week's calendar
    if events_by_day:
        lines.append("### 이번주 일정")
        for d in sorted(events_by_day):
            tag = " ← 오늘" if d == today_str else ""
            lines.append(f"**{d}{tag}**")
            lines.extend(f"  {item}" for item in events_by_day[d])

    # Linear In Progress issues
    if issues:
        lines.append("\n### Linear 진행 중")
        for i in issues:
            proj = f" ({i.get('project') or i.get('team', '')})" if (i.get("project") or i.get("team")) else ""
            due = f" [마감 {i['due_date']}]" if i.get("due_date") else ""
            lines.append(f"- {i['identifier']}{proj}{due}: {i['title']}")

    # Priority mail (today = last 24h)
    for m in mails[:5]:
        if not lines or lines[-1] != "\n### 중요 메일 (오늘)":
            lines.append("\n### 중요 메일 (오늘)")
        badge = "🔴" if m["priority"] == "high" else "🟡"
        sender = m["sender"].split("<")[0].strip().strip('"') or m["sender"]
        lines.append(f"- {badge} {m['subject']} / {sender}")

    return "\n".join(lines)


def _get_dashboard_context() -> str:
    global _DASHBOARD_CACHE
    import time as _time
    now = _time.monotonic()
    if _DASHBOARD_CACHE is None or (now - _DASHBOARD_CACHE[0]) > _DASHBOARD_TTL:
        text = _build_dashboard_context()
        _DASHBOARD_CACHE = (now, text)
    return _DASHBOARD_CACHE[1]


def _get_state_context() -> str:
    """Project state registry — 30-minute TTL cache."""
    global _STATE_CACHE
    import time as _time
    now = _time.monotonic()
    if _STATE_CACHE is None or (now - _STATE_CACHE[0]) > _STATE_TTL:
        try:
            from pipeline.project_state import render_state_for_prompt
            text = render_state_for_prompt()
        except Exception:
            text = ""
        _STATE_CACHE = (now, text)
    return _STATE_CACHE[1]


def _build_workdir_section(working_dir: str | None) -> str:
    """If a working directory is set, append its path and top-level listing to the system prompt."""
    if not working_dir:
        return ""
    from pathlib import Path as _P
    p = _P(working_dir).expanduser()
    if not p.is_dir():
        return f"\n## 작업 폴더\n현재 작업 폴더: `{working_dir}` (존재하지 않음 — 경로 확인 필요)\n"
    try:
        entries = sorted(
            [e.name + ("/" if e.is_dir() else "") for e in p.iterdir() if not e.name.startswith(".")]
        )[:40]
        listing = ", ".join(entries) if entries else "(빈 폴더)"
    except Exception:
        listing = "(목록 조회 실패)"
    return (
        f"\n## 작업 폴더 (현재 세션)\n"
        f"현재 작업 폴더: `{p}`\n"
        f"- bash/python/file_read의 기준 디렉터리가 이 폴더로 설정됨. 상대경로로 파일 작업 가능.\n"
        f"- 최상위 항목: {listing}\n"
    )


def build_system(working_dir: str | None = None) -> str:
    """System prompt — static content only (persona, working dir, slash commands, agent guide).

    Excludes dashboard, state, and current_time to stay prompt-caching-friendly.
    Those are built by build_dynamic_preamble() and prepended before the user message.
    """
    global _PERSONA_CACHE
    if _PERSONA_CACHE is None:
        _PERSONA_CACHE = get_persona()
    workdir_section = _build_workdir_section(working_dir)
    try:
        from pipeline.commands import format_commands_for_prompt
        commands_section = format_commands_for_prompt()
    except Exception:
        commands_section = ""
    agent_md = _load_agent_md()

    # User identity: pull display_name and role_summary from user_profile.
    # Falls back to a generic greeting if both persona and profile are empty.
    from pipeline.user_profile import display_name as _dn, role_summary as _rs
    name = _dn()
    role = _rs()
    persona_block = (_PERSONA_CACHE or "").strip()
    role_line = f" — {role}" if role else ""

    if persona_block:
        identity = f"당신은 VEGA입니다 — {name}{role_line}의 개인 에이전트 하네스."
    else:
        identity = (
            f"당신은 VEGA입니다 — {name}{role_line}의 개인 에이전트 하네스. "
            f"아직 페르소나가 채워지지 않았으니 {name}을(를) 알아가면서 사용자에게 도움을 줘라."
        )

    return f"""{identity}
{workdir_section}{commands_section}
{persona_block}

---

{agent_md}
"""


def build_dynamic_preamble() -> str:
    """Dynamic context that changes each turn (time, dashboard, project state) — prepended before the user message.
    Separated from the system prompt to avoid breaking the cache prefix.
    """
    parts: list[str] = []
    now_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    parts.append(f"현재 시각: {now_kst}")

    dashboard = _get_dashboard_context()
    if dashboard.strip():
        if len(dashboard) > _DASHBOARD_MAX_CHARS:
            dashboard = dashboard[:_DASHBOARD_MAX_CHARS] + "\n… (생략됨)"
        parts.append(f"\n## 현재 상황 브리핑\n{dashboard}")

    state = _get_state_context()
    if state.strip():
        parts.append(f"\n## 프로젝트 현황 (decision support)\n{state}")

    return "\n".join(parts)


def _load_agent_md() -> str:
    """Merge data/agents/_default.md + RULES.md + data/agents/{active_provider}.md.

    Load order:
      1. _default.md — immutable constitution set by the deployer (agent cannot modify)
      2. RULES.md    — mutable rules evolved through conversation by user/agent (written via rule_save)
      3. {provider}.md — provider-specific hints

    Returns empty string if file is missing or read fails (safe fallback)."""
    from pipeline.data_paths import agent_md_path
    parts: list[str] = []
    default_path = agent_md_path("_default")
    if default_path.exists():
        try:
            parts.append(default_path.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    # Mutable rules layer — the rule_save tool writes to this file
    rules_path = agent_md_path("RULES")
    if rules_path.exists():
        try:
            rules_text = rules_path.read_text(encoding="utf-8").strip()
            if rules_text:
                parts.append(f"\n---\n\n## 사용자 정의 규칙 (RULES)\n\n{rules_text}")
        except Exception:
            pass
    try:
        from pipeline.llm_gateway import get_active_name
        prov_name = get_active_name()
        prov_path = agent_md_path(prov_name)
        if prov_path.exists():
            text = prov_path.read_text(encoding="utf-8").strip()
            # Strip the first H1 heading (for human identification only, unnecessary for the model)
            if text:
                parts.append(f"\n---\n\n## 프로바이더별 가이드 ({prov_name})\n\n{text}")
    except Exception:
        pass
    return "\n\n".join(p for p in parts if p)


def _build_request(input_items: list, system: str, ce_mode: bool = False, research_mode: bool = False, tier: str | None = None):
    """Build a request matching the active LLM provider (or the given tier).
    Returns: (Request, kind). kind is 'responses' | 'chat_completions' — used to branch SSE parsing.
    ce_mode=True passes only the allowlist excluding local system tools.
    tier ("local"|"cloud"): 2단 라우터 provider 선택. None 이면 active."""
    from pipeline.llm_gateway import build_request
    schemas = get_schemas_for_mode(TOOL_SCHEMAS, ce_mode=ce_mode)
    return build_request(input_items, system, schemas, research_mode=research_mode, tier=tier)


def _iter_sse_lines(resp):
    """Generator that reads line-by-line from an http.client HTTPResponse."""
    buf = b""
    while True:
        chunk = resp.read(1)
        if not chunk:
            break
        buf += chunk
        if buf.endswith(b"\n"):
            yield buf
            buf = b""


def _queue_put(q, value, loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Put into stdlib Queue or asyncio.Queue safely from the SSE worker thread."""
    if loop is not None:
        try:
            loop.call_soon_threadsafe(q.put_nowait, value)
            return
        except RuntimeError:
            pass
    q.put_nowait(value)


def _stream_sse(
    req: urllib.request.Request,
    token_q: "asyncio.Queue[str | None]",
    tool_q: "asyncio.Queue[dict | None]",
    kind: str = "responses",
    stats_out: dict | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Blocking SSE consumer — emits events into two Queues (runs inside an executor).

    Uses http.client directly so connect and read-idle timeouts can be controlled separately.
    The idle timeout prevents OpenRouter/provider SSE streams from staying open forever
    without events, which otherwise leaves VEGA looking stuck and can accumulate worker threads.

    kind:
      'responses'         — OpenAI Responses API SSE (ChatGPT Codex)
      'chat_completions'  — OpenAI ChatCompletions SSE (OpenRouter / LM Studio / Ollama)
      'anthropic'         — Anthropic Messages API SSE (api.anthropic.com /v1/messages)
    """
    import http.client, ssl

    tool_calls: dict[str, dict] = {}
    arg_buffers: dict[str, str] = {}
    cc_tool_calls: dict[int, dict] = {}  # accumulates ChatCompletions tool_calls by index
    anthropic_blocks: dict[int, dict] = {}  # Anthropic content blocks by index (text / tool_use)
    conn = None

    # Single try-finally covers connect + consume — guarantees sentinel(None) even on connect/fallback failure.
    try:
        # 1) Direct http.client connection (unlimited read). Falls back to urlopen on failure.
        try:
            url = req.full_url
            is_https = url.startswith("https://")
            host_port = url.split("//", 1)[1].split("/", 1)[0]
            path = "/" + url.split("//", 1)[1].split("/", 1)[1] if "/" in url.split("//", 1)[1] else "/"
            ConnCls = http.client.HTTPSConnection if is_https else http.client.HTTPConnection
            # certifi 명시 context — PyInstaller 번들/깨끗한 사용자 맥에서도 CA 검증 통과
            ctx = certified_context() if is_https else None
            conn = ConnCls(host_port, context=ctx, timeout=_SSE_CONNECT_TIMEOUT) if is_https else ConnCls(host_port, timeout=_SSE_CONNECT_TIMEOUT)
            conn.connect()
            conn.sock.settimeout(_SSE_IDLE_TIMEOUT)
            conn.request(req.get_method(), path, body=req.data, headers=dict(req.headers))
            resp = conn.getresponse()
            if resp.status != 200:
                body = resp.read(500).decode(errors="replace")
                raise RuntimeError(f"API HTTP {resp.status}: {body}")
            line_iter = _iter_sse_lines(resp)
        except Exception as conn_err:
            logger.warning("http.client connect failed, falling back to urlopen: %s", conn_err)
            if conn:
                try: conn.close()
                except Exception: pass  # cxt-ignore: exception_hiding
            conn = None
            _ufctx = certified_context() if req.full_url.startswith("https://") else None
            line_iter = urllib.request.urlopen(req, timeout=_SSE_IDLE_TIMEOUT, context=_ufctx)

        # 2) Consume SSE lines
        for raw_line in line_iter:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            chunk = line[6:]
            if chunk == "[DONE]":
                # ChatCompletions flow: emit accumulated tool_calls at [DONE]
                if kind == "chat_completions":
                    for tc in cc_tool_calls.values():
                        if tc.get("name"):
                            _queue_put(tool_q, dict(tc), loop)
                break
            try:
                ev = json.loads(chunk)
            except Exception:
                continue

            if kind == "anthropic":
                # Anthropic Messages API SSE: message_start / content_block_start /
                # content_block_delta (text_delta | input_json_delta) / content_block_stop /
                # message_delta (usage, stop_reason) / message_stop
                et = ev.get("type", "")
                if et == "message_start":
                    usage = (ev.get("message") or {}).get("usage") or {}
                    if stats_out is not None:
                        stats_out["input_tokens"] = stats_out.get("input_tokens", 0) + usage.get("input_tokens", 0)
                        stats_out["cached_tokens"] = stats_out.get("cached_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                        stats_out["cache_write_tokens"] = stats_out.get("cache_write_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
                        model_id = (ev.get("message") or {}).get("model")
                        if model_id:
                            stats_out["model"] = model_id
                elif et == "content_block_start":
                    idx = ev.get("index", 0)
                    block = ev.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        anthropic_blocks[idx] = {
                            "id": block.get("id", ""),
                            "call_id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "arguments": "",
                        }
                    else:
                        anthropic_blocks[idx] = {"type": "text"}
                elif et == "content_block_delta":
                    idx = ev.get("index", 0)
                    delta = ev.get("delta") or {}
                    dt = delta.get("type")
                    if dt == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            _queue_put(token_q, text, loop)
                    elif dt == "input_json_delta":
                        blk = anthropic_blocks.get(idx)
                        if blk is not None and "arguments" in blk:
                            blk["arguments"] += delta.get("partial_json", "")
                elif et == "content_block_stop":
                    idx = ev.get("index", 0)
                    blk = anthropic_blocks.get(idx)
                    if blk and blk.get("name"):
                        _queue_put(tool_q, {
                            "id": blk["id"], "call_id": blk["call_id"],
                            "name": blk["name"], "arguments": blk["arguments"],
                        }, loop)
                elif et == "message_delta":
                    usage = ev.get("usage") or {}
                    if stats_out is not None and usage:
                        stats_out["output_tokens"] = stats_out.get("output_tokens", 0) + usage.get("output_tokens", 0)
                elif et == "message_stop":
                    break
                elif et == "error":
                    err = ev.get("error") or {}
                    logger.error("Anthropic SSE error: %s", err.get("message", err))
                    break
                continue

            if kind == "chat_completions":
                # OpenAI ChatCompletions SSE: choices[0].delta.{content, tool_calls[]}
                # usage info may arrive in a separate chunk (OpenRouter)
                usage = ev.get("usage")
                if usage:
                    pt = usage.get("prompt_tokens", 0)
                    ct = usage.get("completion_tokens", 0)
                    details = usage.get("prompt_tokens_details") or {}
                    cached = details.get("cached_tokens") or usage.get("cache_read_input_tokens") or 0
                    cache_write = usage.get("cache_creation_input_tokens", 0)
                    logger.info("[usage] in=%d out=%d cached=%d write=%d", pt, ct, cached, cache_write)
                    if stats_out is not None:
                        stats_out["input_tokens"] = stats_out.get("input_tokens", 0) + pt
                        stats_out["output_tokens"] = stats_out.get("output_tokens", 0) + ct
                        stats_out["cached_tokens"] = stats_out.get("cached_tokens", 0) + cached
                        stats_out["cache_write_tokens"] = stats_out.get("cache_write_tokens", 0) + cache_write
                # Response model ID (may vary per round — OpenRouter can fallback to another model)
                model_id = ev.get("model")
                if model_id and stats_out is not None:
                    stats_out["model"] = model_id
                choices = ev.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    _queue_put(token_q, content, loop)
                tcs = delta.get("tool_calls") or []
                for tc in tcs:
                    idx = tc.get("index", 0)
                    if idx not in cc_tool_calls:
                        cc_tool_calls[idx] = {"id": tc.get("id", ""), "call_id": tc.get("id", ""),
                                              "name": "", "arguments": ""}
                    if tc.get("id"):
                        cc_tool_calls[idx]["id"] = tc["id"]
                        cc_tool_calls[idx]["call_id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        cc_tool_calls[idx]["name"] = fn["name"]
                    if fn.get("arguments"):
                        cc_tool_calls[idx]["arguments"] += fn["arguments"]
                continue

            # Responses API
            t = ev.get("type", "")

            if t == "response.output_text.delta":
                delta = ev.get("delta", "")
                if delta:
                    _queue_put(token_q, delta, loop)

            elif t == "response.output_item.added":
                item = ev.get("item", {})
                if item.get("type") == "function_call":
                    iid = item["id"]
                    tool_calls[iid] = {
                        "id": iid,
                        "call_id": item.get("call_id", iid),
                        "name": item.get("name", ""),
                        "arguments": "",
                    }
                    arg_buffers[iid] = ""

            elif t == "response.function_call_arguments.delta":
                iid = ev.get("item_id", "")
                if iid in arg_buffers:
                    arg_buffers[iid] += ev.get("delta", "")

            elif t == "response.function_call_arguments.done":
                iid = ev.get("item_id", "")
                if iid in tool_calls:
                    tool_calls[iid]["arguments"] = ev.get(
                        "arguments", arg_buffers.get(iid, "")
                    )
                    _queue_put(tool_q, dict(tool_calls[iid]), loop)

            elif t == "response.completed":
                # ChatGPT Responses API: response.usage.{input_tokens, output_tokens, ...}
                resp = ev.get("response") or {}
                usage = resp.get("usage") or {}
                pt = usage.get("input_tokens", 0)
                ct = usage.get("output_tokens", 0)
                details = usage.get("input_tokens_details") or {}
                cached = details.get("cached_tokens", 0)
                logger.info("[usage] in=%d out=%d cached=%d", pt, ct, cached)
                if stats_out is not None:
                    stats_out["input_tokens"] = stats_out.get("input_tokens", 0) + pt
                    stats_out["output_tokens"] = stats_out.get("output_tokens", 0) + ct
                    stats_out["cached_tokens"] = stats_out.get("cached_tokens", 0) + cached
                    model_id = resp.get("model")
                    if model_id:
                        stats_out["model"] = model_id

    except Exception as e:
        if stats_out is not None:
            stats_out["stream_error"] = str(e)
        logger.error("SSE streaming error: %s", e)
    finally:
        _queue_put(token_q, None, loop)
        _queue_put(tool_q, None, loop)
        if conn:
            try: conn.close()
            except Exception: pass  # cxt-ignore: exception_hiding


async def stream_gpt(
    messages: list[dict],
    system: str,
    on_token,
    on_tool_start=None,
    on_tool_done=None,
    on_consent=None,
    on_waiting=None,
    images: list[dict] | None = None,
    working_dir: str | None = None,
    stats: dict | None = None,
    plan_mode: bool = False,
    ce_mode: bool = False,
    research_mode: bool = False,
    tier: str | None = None,
    spawn_context: dict | None = None,
) -> str:
    """
    GPT tool-use loop — streams SSE tokens to on_token in real time.
    on_waiting: called once right after executor starts, before the first token (for loading indicator).
    images: [{"data": "base64...", "media_type": "image/png"}, ...] — attached to the first user message.
    working_dir: session working directory — injected as cwd during tool execution (defaults to home if None).
    plan_mode: True blocks write, exec, and external-send tools at the dispatch layer.
    ce_mode: True restricts tool schemas sent to the LLM to the CE allowlist (remote client).
    research_mode: True allows up to 40 tool-loop rounds and passes a hint to build_request.
    """
    loop = asyncio.get_event_loop()

    if len(messages) == 1:
        user_content = messages[-1]["content"]
    else:
        turns = []
        for m in messages[:-1]:
            label = "User" if m["role"] in ("user", "human") else "VEGA"
            turns.append(f"[{label}]: {m['content']}")
        history_block = "\n".join(turns)
        user_content = f"[대화 히스토리]\n{history_block}\n\n[현재 메시지]\n{messages[-1]['content']}"

    # Dynamic context (time, dashboard, state) is prepended before the user message.
    # System prompt stays static so prompt caching can hit.
    preamble = build_dynamic_preamble()
    if preamble.strip():
        user_content = f"{preamble}\n\n---\n\n{user_content}"

    # If images are present, convert content to list format
    if images:
        content_blocks: list = [{"type": "input_text", "text": user_content}]
        for img in images:
            media_type = img.get("media_type", "image/png")
            data = img.get("data", "")
            content_blocks.append({
                "type": "input_image",
                "image_url": f"data:{media_type};base64,{data}",
            })
        input_items: list = [{"role": "user", "content": content_blocks}]
    else:
        input_items: list = [{"role": "user", "content": user_content}]
    full_text = ""
    max_rounds = 40 if research_mode else 20
    first_round = True

    # for timing measurement
    import time as _t
    t_start = _t.monotonic()
    t_first_token: float | None = None

    for _ in range(max_rounds):
        req, kind = _build_request(input_items, system, ce_mode=ce_mode, research_mode=research_mode, tier=tier)

        token_q: asyncio.Queue = asyncio.Queue()
        tool_q: asyncio.Queue = asyncio.Queue()

        sse_task = loop.run_in_executor(None, _stream_sse, req, token_q, tool_q, kind, stats, loop)

        # Show "thinking" indicator at the start of each round — so the UI doesn't appear frozen
        # while the model deliberates before responding after a tool call.
        # The frontend's hideThinking removes it automatically on the first token.
        if on_waiting:
            await on_waiting()
        first_round = False

        round_text = ""
        pending_tools: list[dict] = []

        token_done = tool_done = False
        while not (token_done and tool_done):
            drained = False

            if not token_done:
                try:
                    tok = token_q.get_nowait()
                    if tok is None:
                        token_done = True
                    else:
                        if t_first_token is None:
                            t_first_token = _t.monotonic()
                        round_text += tok
                        full_text += tok
                        await on_token(tok)
                    drained = True
                except asyncio.QueueEmpty:
                    pass

            if not tool_done:
                try:
                    tc = tool_q.get_nowait()
                    if tc is None:
                        tool_done = True
                    else:
                        pending_tools.append(tc)
                    drained = True
                except asyncio.QueueEmpty:
                    pass

            if not drained:
                await asyncio.sleep(0.005)

        await sse_task

        stream_error = stats.get("stream_error") if stats is not None else None
        if stream_error and not round_text and not pending_tools:
            raise RuntimeError(f"LLM 스트림 오류: {stream_error}")

        if not pending_tools:
            break

        # Preserve the assistant text from a tool-calling round in history. If dropped,
        # the model re-emits the same intro every round (echo) — INT-1411 regression.
        if round_text.strip():
            input_items.append({"role": "assistant", "content": round_text})

        for tc in pending_tools:
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except Exception:
                args = {}

            call_id = tc["call_id"]

            if on_tool_start:
                await on_tool_start(name, args, call_id)

            # Permission consent gate (INT-1386) — dangerous-level tools (delete/order/send
            # per policy) require user approval before execution. Separate from host_exec's
            # __needs_approval__ path (host_exec/bash/python have their own ask flow).
            if on_consent and name not in ("host_exec", "bash_exec", "python_exec"):
                try:
                    from pipeline.permission import requires_consent
                    need = requires_consent(name)
                except Exception:
                    need = False
                if need:
                    granted = await on_consent(name, args, call_id)
                    if not granted:
                        result = json.dumps(
                            {"status": "denied", "output": "User did not approve this action."},
                            ensure_ascii=False,
                        )
                        if on_tool_done:
                            await on_tool_done(name, result, call_id)
                        input_items.append({
                            "type": "function_call", "id": tc["id"], "call_id": tc["call_id"],
                            "name": name, "arguments": tc["arguments"],
                        })
                        input_items.append({
                            "type": "function_call_output", "call_id": tc["call_id"], "output": result,
                        })
                        continue

            try:
                # Set working directory before tool execution in the thread
                # - tools_code: cwd for host_exec/file_read
                # - sandbox: routes bash_exec/python_exec to container with /project rw mount
                def _dispatch_with_cwd():
                    from pipeline.tools_code import set_session_working_dir
                    from pipeline.sandbox import set_sandbox_project_dir
                    from pipeline.tools import set_plan_mode, set_ce_mode
                    from pipeline.spawn import clear_dispatch_context, set_dispatch_context
                    set_session_working_dir(working_dir)
                    set_sandbox_project_dir(working_dir)
                    set_plan_mode(plan_mode)
                    set_ce_mode(ce_mode)
                    if spawn_context:
                        set_dispatch_context(**spawn_context)
                    try:
                        return dispatch_tool(name, args)
                    finally:
                        if spawn_context:
                            clear_dispatch_context()
                result = await loop.run_in_executor(None, _dispatch_with_cwd)
            except Exception as e:
                result = json.dumps({"error": str(e)}, ensure_ascii=False)

            # On tool failure, check whether to trigger self-improvement
            try:
                parsed_result = json.loads(result) if isinstance(result, str) else result
                if isinstance(parsed_result, dict) and parsed_result.get("error"):
                    from pipeline.self_improve import should_improve, attempt_improvement
                    if should_improve(name):
                        improvement = await attempt_improvement(name)
                        if improvement and on_tool_done:
                            await on_tool_done(f"__improve__{name}", json.dumps(improvement, ensure_ascii=False), call_id)
            except Exception:
                pass

            if on_tool_done:
                override = await on_tool_done(name, result, call_id)
                if override is not None:
                    result = override  # replace with actual execution result (e.g. from approval flow)

            input_items.append({
                "type": "function_call",
                "id": tc["id"],
                "call_id": tc["call_id"],
                "name": name,
                "arguments": tc["arguments"],
            })
            input_items.append({
                "type": "function_call_output",
                "call_id": tc["call_id"],
                "output": result,
            })

    # Augment timing stats
    if stats is not None:
        t_end = _t.monotonic()
        elapsed = max(0.001, t_end - (t_first_token or t_start))
        out = stats.get("output_tokens", 0)
        stats["elapsed_sec"] = round(t_end - t_start, 2)
        stats["gen_sec"] = round(elapsed, 2)
        stats["tok_per_sec"] = round(out / elapsed, 1) if out else 0
        stats["ttft_sec"] = round((t_first_token or t_end) - t_start, 2)
    return full_text
