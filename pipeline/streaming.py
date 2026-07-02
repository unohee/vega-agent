# Created: 2026-05-18
# Purpose: VEGA GPT tool-use streaming loop — no Chainlit dependency
# Dependencies: pipeline/tools.py, pipeline/vega_query.py, pipeline/auth/chatgpt.py
# Test Status: under validation

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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
_AGENT_MD_CACHE: tuple[tuple[tuple[str, int | None], ...], str] | None = None


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


def _load_light_fragment() -> str:
    p = Path(__file__).resolve().parent.parent / "data" / "agents" / "_light.md"
    if p.is_file():
        return p.read_text(encoding="utf-8").strip()
    return ""


def build_system(working_dir: str | None = None, load: str | None = None) -> str:
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

    base = f"""{identity}
{workdir_section}{commands_section}
{persona_block}

---

{agent_md}
"""
    if load == "light":
        frag = _load_light_fragment()
        if frag:
            return base + f"\n\n---\n\n{frag}\n"
    return base


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


def invalidate_agent_md_cache() -> None:
    global _AGENT_MD_CACHE
    _AGENT_MD_CACHE = None


def _load_agent_md() -> str:
    """Merge data/agents/_default.md + RULES.md + data/agents/{active_provider}.md.

    Load order:
      1. _default.md — immutable constitution set by the deployer (agent cannot modify)
      2. RULES.md    — mutable rules evolved through conversation by user/agent (written via rule_save)
      3. {provider}.md — provider-specific hints

    Returns empty string if file is missing or read fails (safe fallback)."""
    from pipeline.data_paths import agent_md_path
    default_path = agent_md_path("_default")
    rules_path = agent_md_path("RULES")
    try:
        from pipeline.llm_gateway import get_active_name
        prov_name = get_active_name()
    except Exception:
        prov_name = ""
    prov_path = agent_md_path(prov_name) if prov_name else None
    paths = [default_path, rules_path]
    if prov_path is not None:
        paths.append(prov_path)
    sig = tuple((str(p), p.stat().st_mtime_ns if p.exists() else None) for p in paths)
    global _AGENT_MD_CACHE
    if _AGENT_MD_CACHE and _AGENT_MD_CACHE[0] == sig:
        return _AGENT_MD_CACHE[1]

    parts: list[str] = []
    if default_path.exists():
        try:
            parts.append(default_path.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    # Mutable rules layer — the rule_save tool writes to this file
    if rules_path.exists():
        try:
            rules_text = rules_path.read_text(encoding="utf-8").strip()
            if rules_text:
                parts.append(f"\n---\n\n## 사용자 정의 규칙 (RULES)\n\n{rules_text}")
        except Exception:
            pass
    if prov_name and prov_path is not None:
        try:
            text = prov_path.read_text(encoding="utf-8").strip() if prov_path.exists() else ""
            # Strip the first H1 heading (for human identification only, unnecessary for the model)
            if text:
                parts.append(f"\n---\n\n## 프로바이더별 가이드 ({prov_name})\n\n{text}")
        except Exception:
            pass
    result = "\n\n".join(p for p in parts if p)
    _AGENT_MD_CACHE = (sig, result)
    return result


def _build_request(input_items: list, system: str, ce_mode: bool = False, research_mode: bool = False, tier: str | None = None, model_override: str | None = None, load: str | None = None):
    """Build a request matching the active LLM provider (or the given tier).
    Returns: (Request, kind). kind is 'responses' | 'chat_completions' — used to branch SSE parsing.
    ce_mode=True passes only the allowlist excluding local system tools.
    tier ("local"|"cloud"): 2단 라우터 provider 선택. None 이면 active."""
    from pipeline.llm_gateway import build_request
    schemas = get_schemas_for_mode(TOOL_SCHEMAS, ce_mode=ce_mode, load=load)
    return build_request(input_items, system, schemas, research_mode=research_mode, tier=tier, model_override=model_override, load=load)


# 모델 tokenizer 특수토큰 — 정상 본문엔 절대 나오지 않는다. 모델이 degenerate 하면
# 본문에 누수돼 외계어처럼 보인다(ST 4294: deepseek-flash 가 raw vocab 을 토해낸 사례).
# fullwidth 파이프(｜ U+FF5C)를 쓰는 <｜…｜> 류는 DeepSeek 토크나이저 시그니처라 항상 제거.
# ascii <|begin_of_sentence|> / <|place_holder…|> 등 알려진 이름도 제거. 일반 마크업은 건드리지 않는다.
_MODEL_ARTIFACT_RE = re.compile(
    r"<｜[^>｜]{0,60}｜>"
    r"|<\|(?:begin|end)[_▁]of[_▁]sentence\|>"
    r"|<\|place[_▁]?holder[^>]{0,30}\|>"
)


def _strip_model_artifacts(text: str) -> tuple[str, int]:
    """본문 토큰에서 모델 특수토큰을 제거. 반환 (정리된 텍스트, 제거 개수)."""
    if "<｜" not in text and "<|" not in text:
        return text, 0
    n = 0

    def _sub(_m):
        nonlocal n
        n += 1
        return ""

    return _MODEL_ARTIFACT_RE.sub(_sub, text), n


# Degeneration 안전망 (INT-1999 b). (a) penalty 가 주 처방이고, 이것은 그래도 새는
# 잔여 케이스를 잡는 백엔드 안전망 — 도구 라운드의 중간 텍스트가 degenerate 하면
# history 오염을 막고(round_text 폐기) auto_route 면 다음 라운드부터 sturdier 모델로 전환.
_DEGEN_MAX_RETRIES = 1


def _detect_degeneration(text: str, artifact_count: int = 0, *, artifact_threshold: int = 5) -> bool:
    """라운드 텍스트가 degeneration(특수토큰 누수·반복 붕괴)인지 휴리스틱 판정.

    오탐을 피하려 보수적으로 — 정상 텍스트(다양한 어휘)는 통과시킨다.
    artifact_count: 이 라운드에서 strip 된 모델 특수토큰 누적 수."""
    if artifact_count >= artifact_threshold:
        return True
    if len(text) < 300:
        return False  # 짧은 텍스트는 반복 판단 불가 — 보류
    tail = text[-400:]
    words = tail.split()
    if len(words) >= 20 and len(set(words)) / len(words) < 0.25:
        return True  # 어휘 다양성 붕괴(같은 단어/구절 반복)
    seg = tail[-40:]
    if len(seg) == 40 and tail.count(seg) >= 3:
        return True  # 동일 40자 구절 3회+ 반복
    return False


def _sturdier_model(current: str | None) -> str | None:
    """auto_route 활성 시 더 견고한(heavy) 모델 id. 아니면 None(수동 선택 존중 → 전환 안 함)."""
    try:
        from pipeline.model_catalog import resolve_turn_model
        m = resolve_turn_model("heavy")
        if m and m != current:
            return m
    except Exception:
        pass
    return None


def _iter_sse_lines(resp):
    """Generator that reads line-by-line from an http.client HTTPResponse."""
    readline = getattr(resp, "readline", None)
    if callable(readline):
        while True:
            line = readline()
            if not line:
                break
            yield line
        return

    buf = b""
    while True:
        chunk = resp.read(8192)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line + b"\n"
    if buf:
        yield buf


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
    reasoning_q: "asyncio.Queue[dict | None] | None" = None,
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
                            text, _na = _strip_model_artifacts(text)
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
                    content, _na = _strip_model_artifacts(content)
                    if _na:
                        if stats_out is not None:
                            stats_out["artifact_count"] = stats_out.get("artifact_count", 0) + _na
                        logger.warning("[degeneration] 모델 특수토큰 %d개 제거 (model=%s)",
                                       _na, (stats_out or {}).get("model", "?"))
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
                    delta, _na = _strip_model_artifacts(delta)
                    if _na and stats_out is not None:
                        stats_out["artifact_count"] = stats_out.get("artifact_count", 0) + _na
                    if delta:
                        _queue_put(token_q, delta, loop)

            elif t == "response.reasoning_summary_text.delta":
                delta = ev.get("delta", "")
                if delta and reasoning_q is not None:
                    _queue_put(reasoning_q, {"delta": delta, "done": False}, loop)

            elif t == "response.reasoning_summary_text.done":
                text = ev.get("text", "")
                if reasoning_q is not None:
                    _queue_put(reasoning_q, {"delta": text, "done": True}, loop)

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
        if reasoning_q is not None:
            _queue_put(reasoning_q, None, loop)
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
    on_reasoning=None,
    images: list[dict] | None = None,
    working_dir: str | None = None,
    stats: dict | None = None,
    plan_mode: bool = False,
    ce_mode: bool = False,
    research_mode: bool = False,
    tier: str | None = None,
    spawn_context: dict | None = None,
    load_override: str | None = None,
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

    try:
        from pipeline.tools_web import clear_web_search_cache
        clear_web_search_cache()
    except Exception:
        pass

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
    # 부하별 라운드 상한·모델 선택 — 현재 user 메시지만 분류 (INT-1893/1892).
    load = "standard"
    model_override: str | None = None
    max_rounds = 40 if research_mode else 20
    max_tool_rounds = 24
    try:
        from pipeline.model_catalog import resolve_turn_model
        from pipeline.tier_router import resolve_load_routing

        routing = resolve_load_routing(messages, research_mode=research_mode, load_override=load_override)
        load = routing["load"]
        max_rounds = routing["max_rounds"]
        max_tool_rounds = routing["max_tool_rounds"]
        if tier != "local":
            model_override = resolve_turn_model(load)
        if stats is not None:
            stats["load"] = load
            stats["max_rounds"] = max_rounds
            stats["max_tool_rounds"] = max_tool_rounds
            stats["route_text_len"] = len(routing["route_text"])
            if model_override:
                stats["selected_model"] = model_override
            if routing.get("budget"):
                stats["budget_max_tokens"] = routing["budget"].get("max_tokens")
    except Exception:
        pass
    if load == "light" and system and "Light load mode" not in system:
        frag = _load_light_fragment()
        if frag and frag not in system:
            system = system + f"\n\n---\n\n{frag}\n"
    first_round = True
    actual_rounds = 0
    tool_rounds = 0
    suppress_tools = False
    degen_retries = 0

    # for timing measurement
    import time as _t
    t_start = _t.monotonic()
    t_first_token: float | None = None

    for _round in range(max_rounds):
        actual_rounds += 1
        req, kind = _build_request(
            input_items, system, ce_mode=ce_mode, research_mode=research_mode,
            tier=tier, model_override=model_override, load=load,
        )
        if suppress_tools:
            import json as _json
            body = _json.loads(req.data.decode())
            body.pop("tools", None)
            body.pop("tool_choice", None)
            req.data = _json.dumps(body).encode()

        token_q: asyncio.Queue = asyncio.Queue()
        tool_q: asyncio.Queue = asyncio.Queue()
        reasoning_q: asyncio.Queue | None = asyncio.Queue() if on_reasoning else None

        if reasoning_q is not None:
            sse_task = loop.run_in_executor(None, _stream_sse, req, token_q, tool_q, kind, stats, loop, reasoning_q)
        else:
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
        reasoning_done = reasoning_q is None
        while not (token_done and tool_done and reasoning_done):
            drained = False

            if reasoning_q is not None and not reasoning_done:
                try:
                    item = reasoning_q.get_nowait()
                    if item is None:
                        reasoning_done = True
                    else:
                        delta = item.get("delta", "") if isinstance(item, dict) else str(item)
                        done = bool(item.get("done")) if isinstance(item, dict) else False
                        if delta or done:
                            await on_reasoning(delta, done)
                    drained = True
                except asyncio.QueueEmpty:
                    pass

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
            # 최종 답변 라운드 degeneration 안전망 (INT-2269 d, TECH #4322): 도구 없는
            # 최종 답변이 degenerate 하면 sturdier 모델로 재생성한다. 도구-라운드 안전망
            # (아래 861-)과 동일한 헬퍼·상수·stats 규약을 재사용한다. 이미 on_token 으로
            # 방출된 오염 델타는 full_text 에서 잘라내고(round_text 는 full_text 끝에 누적),
            # break 대신 continue 로 다음 (여전히 no-tool) 라운드에서 최종 답을 다시 만든다.
            # 프론트 롤백(방출 델타 지우기)은 별도 이슈 — 여기선 재생성 답이 이어서 스트리밍된다.
            _artifact_n = stats.get("artifact_count", 0) if stats is not None else 0
            _sturdier = _sturdier_model(model_override)
            if (
                degen_retries < _DEGEN_MAX_RETRIES
                and _detect_degeneration(round_text, _artifact_n)
                and _sturdier is not None
            ):
                if stats is not None:
                    stats["degenerated"] = True
                    stats["degen_final_round"] = True
                    stats.setdefault("degen_rounds", []).append(
                        {
                            "round": actual_rounds,
                            "model": stats.get("model"),
                            "final": True,
                            "artifacts": _artifact_n,
                        }
                    )
                    stats["degen_switched_to"] = _sturdier
                model_override = _sturdier
                degen_retries += 1
                # 이미 방출된 오염 텍스트를 full_text 에서 제거 (round_text 가 끝에 누적됨)
                if round_text:
                    full_text = full_text[: len(full_text) - len(round_text)]
                round_text = ""
                if stats is not None:
                    stats["artifact_count"] = 0
                continue  # sturdier 모델로 최종 답변 재생성 (다음 라운드도 no-tool 최종 시도)
            break

        tool_rounds += 1
        if tool_rounds >= max_tool_rounds:
            input_items.append({
                "role": "user",
                "content": "[시스템] 도구 호출 상한에 도달했다. 지금까지 결과만으로 최종 답변을 3–8문장으로 마무리하라.",
            })
            if stats is not None:
                stats["tool_round_cap_hit"] = True
            suppress_tools = True

        # Degeneration 안전망 (INT-1999 b): 도구 라운드의 중간 텍스트가 degenerate 하면
        # history 에 넣지 않고(오염 전파 차단) auto_route 면 다음 라운드부터 sturdier 모델로 전환.
        # 최종 답변 라운드(no tools)의 degeneration 은 (a) penalty 에 의존 — 프론트 롤백 비용 때문에 제외.
        _artifact_n = stats.get("artifact_count", 0) if stats is not None else 0
        if degen_retries < _DEGEN_MAX_RETRIES and _detect_degeneration(round_text, _artifact_n):
            if stats is not None:
                stats["degenerated"] = True
                stats.setdefault("degen_rounds", []).append(
                    {"round": actual_rounds, "model": stats.get("model"), "artifacts": _artifact_n}
                )
            _sturdier = _sturdier_model(model_override)
            if _sturdier:
                model_override = _sturdier
                degen_retries += 1
                if stats is not None:
                    stats["degen_switched_to"] = _sturdier
            round_text = ""  # 오염 텍스트는 history 에 넣지 않는다
            if stats is not None:
                stats["artifact_count"] = 0  # 다음 라운드용 카운터 리셋

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

            if stats is not None and name:
                stats.setdefault("tools_called", []).append(name)

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
                # - tools_code: cwd for host_exec/file_read/bash_exec/python_exec
                def _dispatch_with_cwd():
                    from pipeline.tools_code import set_session_working_dir
                    from pipeline.tools import set_plan_mode, set_ce_mode
                    from pipeline.spawn import clear_dispatch_context, set_dispatch_context
                    set_session_working_dir(working_dir)
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

            try:
                parsed_result = json.loads(result) if isinstance(result, str) else result
            except Exception:
                parsed_result = None

            # INT-1895: 코드 실행 성공 → 스킬 저장 런타임 신호
            if stats is not None and name in ("python_exec", "sandbox_save_module"):
                if isinstance(parsed_result, dict) and not parsed_result.get("error"):
                    stats["suggest_skill_save"] = True

            # On tool failure, check whether to trigger self-improvement
            try:
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

            if load == "light" and name == "web_search" and not suppress_tools:
                hits = parsed_result if isinstance(parsed_result, list) else []
                if len(hits) >= 3:
                    input_items.append({
                        "role": "user",
                        "content": "[시스템] 검색 결과가 충분하다. 추가 도구 없이 추천/답변만 작성하라.",
                    })
                    suppress_tools = True
                    if stats is not None:
                        stats["early_stop_search"] = True

    # Augment timing stats
    if stats is not None:
        t_end = _t.monotonic()
        elapsed = max(0.001, t_end - (t_first_token or t_start))
        out = stats.get("output_tokens", 0)
        stats["actual_rounds"] = actual_rounds
        stats["tool_rounds"] = tool_rounds
        stats["elapsed_sec"] = round(t_end - t_start, 2)
        stats["gen_sec"] = round(elapsed, 2)
        stats["tok_per_sec"] = round(out / elapsed, 1) if out else 0
        stats["ttft_sec"] = round((t_first_token or t_end) - t_start, 2)
    return full_text
