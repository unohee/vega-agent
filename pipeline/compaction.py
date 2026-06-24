# Created: 2026-05-18
# Purpose: conversation history compaction + automatic long/short-term memory update
# Dependencies: pipeline/streaming.py, pipeline/tools.py, pipeline/auth/chatgpt.py

from __future__ import annotations

import asyncio
import json
import logging
import sys
import urllib.request
from pathlib import Path

from pipeline.auth.chatgpt import (
    CODEX_BASE_URL,
    DEFAULT_CODEX_MODEL,
    _load_profile,
    ensure_valid_token,
)
from pipeline.streaming import _build_request, _stream_sse
from pipeline.tools import TOOL_SCHEMAS, dispatch_tool

logger = logging.getLogger(__name__)

# Compaction trigger — 토큰 기준이 주 트리거, 메시지 수는 백스톱 (INT-1430).
# 기존 "메시지 20개" 고정 트리거는 짧은 메시지 위주 대화에서도 응답 사이에
# 수십 초짜리 요약 LLM 호출을 유발했다. 실제 비용(요청 크기·지연)은 토큰이
# 결정하므로 토큰 기준으로 바꾸고, 메시지 수는 안전망으로만 둔다.
COMPACT_TOKEN_THRESHOLD = 8000
COMPACT_THRESHOLD = 60
# Number of recent messages to preserve after compaction (keeps conversation context post-summary)
KEEP_RECENT = 6
# 메모리 자동 업데이트(요약 시 persona/event/rule 갱신) 기본 on
AUTO_MEMORY_UPDATE = True

# 설정 기본값 — memory_settings.json이 없거나 키가 빠졌을 때 사용(하위호환).
# 토큰 임계값(COMPACT_TOKEN_THRESHOLD)은 주 트리거로 고정이고, 메시지 수 백스톱
# (compact_threshold)·보존 수(keep_recent)·자동 메모리 갱신만 사용자 설정 대상.
_SETTINGS_DEFAULTS = {
    "compact_threshold": COMPACT_THRESHOLD,
    "keep_recent": KEEP_RECENT,
    "auto_memory_update": AUTO_MEMORY_UPDATE,
}


def load_memory_settings() -> dict:
    """memory_settings.json을 매번 읽어 반환(핫리로드). 없거나 깨지면 기본값."""
    from pipeline.data_paths import memory_settings_path
    p = memory_settings_path()
    out = dict(_SETTINGS_DEFAULTS)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for k in _SETTINGS_DEFAULTS:
                if k in data:
                    out[k] = data[k]
        except Exception:
            pass
    return out


def save_memory_settings(settings: dict) -> dict:
    """memory_settings.json 저장. 알려진 키만 기록(검증). 저장된 전체 설정 반환."""
    from pipeline.data_paths import memory_settings_path
    cur = load_memory_settings()
    if "compact_threshold" in settings:
        cur["compact_threshold"] = max(4, int(settings["compact_threshold"]))
    if "keep_recent" in settings:
        cur["keep_recent"] = max(2, int(settings["keep_recent"]))
    if "auto_memory_update" in settings:
        cur["auto_memory_update"] = bool(settings["auto_memory_update"])
    # keep_recent는 threshold보다 작아야 의미가 있다.
    if cur["keep_recent"] >= cur["compact_threshold"]:
        cur["keep_recent"] = max(2, cur["compact_threshold"] - 2)
    p = memory_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return cur


def _keep_recent() -> int:
    return load_memory_settings()["keep_recent"]


def _estimate_tokens(history: list[dict]) -> int:
    """Token count for history — uses tiktoken (cl100k_base)."""
    from pipeline.token_count import count_message_tokens
    return count_message_tokens(history)


def _needs_compaction(history: list[dict]) -> bool:
    if not history:
        return False
    if len(history) >= load_memory_settings()["compact_threshold"]:
        return True
    return _estimate_tokens(history) >= COMPACT_TOKEN_THRESHOLD


def splice_compacted(live: list[dict], summary_msg: dict, n_summarized: int) -> None:
    """백그라운드 압축 완료 시 live 히스토리에 요약을 in-place 접합.

    compact_history는 스냅샷의 앞 n_summarized개만 요약하므로, 압축이 도는 동안
    live에 새로 추가된 메시지(live[n_summarized:])는 그대로 보존된다.
    clear+extend로 list 객체 identity를 유지해야 _SESSION_HISTORY와
    진행 중인 핸들러가 들고 있는 참조가 깨지지 않는다."""
    tail = live[n_summarized:]
    live.clear()
    live.extend([summary_msg, *tail])



def _compact_system() -> str:
    from pipeline.user_profile import display_name as _dn
    name = _dn()
    return f"""너는 대화 요약·메모리 추출·행동 규칙 회고 전문가다.

주어진 대화 히스토리를 분석해서 세 가지 작업을 수행한다:

1. **대화 요약**: 중요한 결정, 결론, 맥락, 미해결 항목을 한국어로 간결하게 요약.
   - 포맷: 마크다운 불릿 리스트
   - 길이: 300~600자 이내
   - 포함: 주요 작업 완료 사항, 결정된 방향, 언급된 중요 인물/프로젝트

2. **메모리 업데이트**: 대화에서 드러난 정보 중 장기 저장이 필요한 것을 메모리 도구로 저장.
   - {name}에 대해 새로 알게 된 사실 → memory_persona_update
   - 중요 이벤트/결정 → memory_event_add
   - 새 인물/조직 등장 → memory_entity_upsert

3. **행동 교정 회고 (RULES)**: 사용자가 이번 대화에서 VEGA의 응답·행동을 **지속적으로 바꾸라**고
   지시한 부분이 있는지 점검한다. 있으면 `rule_save`로 저장한다.

   감지 기준 (이 신호가 하나라도 있어야 저장):
   - "앞으로 ~해" / "앞으로는 ~" / "항상 ~" / "절대 ~하지 마"
   - "기억해줘 — 규칙으로" / "규칙으로 만들어"
   - 같은 지시·교정이 두 번 이상 반복됨
   - "방금 그 말투/형식 저장해"

   ❌ **저장하지 않을 것:**
   - 이번 한 번만 적용된 요청 (1회성 톤 조정 등)
   - 메모리(사실)에 가까운 정보 (그건 memory_persona_update로 분류)
   - 명확한 규칙으로 추상화하기 어려운 모호한 피드백
   - 이미 RULES.md에 비슷한 규칙이 있는 경우 (직접 확인 어려우면 보류)

   rule_id는 의미를 알 수 있는 소문자-하이픈 (예: `code-review-style`, `email-formal-tone`).
   section은 응답 스타일 / 도구 사용 / 도메인 규칙 / 커뮤니케이션 / 보안·민감 정보 중 선택.

반드시 요약 텍스트를 먼저 출력하고, 그 다음 메모리·규칙 도구를 호출한다.
3번에서 감지된 규칙이 없으면 `rule_save`는 호출하지 않는다 — 억지로 만들지 마라."""

# Expose only memory + rule tools to the compaction LLM
_MEMORY_SCHEMAS = [
    s for s in TOOL_SCHEMAS
    if s.get("name", "").startswith("memory_") or s.get("name", "").startswith("rule_")
]


async def compact_history(
    history: list[dict],
    on_status: "asyncio.coroutines | None" = None,
) -> tuple[list[dict], str]:
    """
    Run history compaction.

    Returns: (new_history, summary_text)
    - new_history: [{"role":"system","content":"[SUMMARY] ..."}] + history[-keep_recent:]
    - summary_text: for frontend display
    """
    if on_status:
        await on_status("📦 대화 압축 중…")

    # Messages to summarize: all except the last keep_recent (memory_settings.json 핫리로드)
    keep = _keep_recent()
    to_summarize = history[:-keep] if len(history) > keep else history
    recent = history[-keep:] if len(history) > keep else []

    # Serialize conversation to text
    from pipeline.user_profile import display_name as _dn
    user_label = _dn()
    turns = []
    for m in to_summarize:
        role = user_label if m["role"] in ("user", "human") else "VEGA"
        content = str(m.get("content", ""))[:800]  # truncate overly long messages
        turns.append(f"[{role}]: {content}")
    history_text = "\n".join(turns)

    input_items = [{"role": "user", "content": f"다음 대화를 요약하고 필요한 메모리를 업데이트해줘:\n\n{history_text}"}]

    loop = asyncio.get_event_loop()
    try:
        summary_text, tool_calls = await loop.run_in_executor(
            None, _call_compact_sync, input_items, _compact_system()
        )
    except Exception as e:
        logger.warning(f"Compaction LLM call failed: {e}")
        # 실패 시 history 불변 — to_summarize를 버리지 않는다(INT-1523)
        summary = f"[컴팩션 실패 — 히스토리 유지: {e}]"
        return history, summary

    # Execute memory/rule tool calls — whitelist as a second defense (ignore any other tool the compaction model tries to call)
    # auto_memory_update가 off면 요약만 하고 persona/event/rule 갱신은 건너뛴다.
    saved_rules: list[str] = []
    if not load_memory_settings().get("auto_memory_update", True):
        tool_calls = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("arguments", {})
        if name.startswith("memory_"):
            try:
                dispatch_tool(name, args)
                logger.info(f"Compaction memory saved: {name}({list(args.keys())})")
            except Exception as e:
                logger.warning(f"Compaction memory save failed {name}: {e}")
        elif name == "rule_save":
            try:
                result = dispatch_tool(name, args)
                import json as _json
                parsed = _json.loads(result) if isinstance(result, str) else result
                if isinstance(parsed, dict) and parsed.get("ok"):
                    rid = parsed.get("rule_id", args.get("rule_id", "?"))
                    saved_rules.append(rid)
                    logger.info(f"Compaction rule saved: {rid} (section={args.get('section', '?')})")
                else:
                    logger.warning(f"Compaction rule save rejected: {parsed}")
            except Exception as e:
                logger.warning(f"Compaction rule save failed: {e}")
        # rule_delete/rule_list are forbidden during compaction (prevents unintended deletion)

    if on_status:
        status_msg = f"✅ 압축 완료 — {len(to_summarize)}턴 → 요약 1개"
        if saved_rules:
            status_msg += f" · 새 규칙 {len(saved_rules)}개 저장 ({', '.join(saved_rules)})"
        await on_status(status_msg)

    summary_block = f"[이전 대화 요약]\n{summary_text}"
    if saved_rules:
        summary_block += f"\n\n[회고에서 추출된 행동 규칙] {', '.join(saved_rules)} — /rules로 확인 가능"
    new_history = [{"role": "assistant", "content": summary_block}] + recent
    return new_history, summary_text


def _call_compact_sync(input_items: list, system: str) -> tuple[str, list[dict]]:
    """
    Calls the compaction LLM via SSE streaming.
    Returns: (summary_text, [{"name": tool_name, "arguments": dict}, ...])
    Blocking — must be called from an executor.
    """
    import queue as _queue

    token_q: _queue.Queue = _queue.Queue()
    tool_q:  _queue.Queue = _queue.Queue()

    req, kind = _build_request(input_items, system)
    _stream_sse(req, token_q, tool_q, kind=kind)

    # Drain token_q
    tokens = []
    while True:
        tok = token_q.get()
        if tok is None:
            break
        tokens.append(tok)

    # Drain tool_q
    calls = []
    while True:
        tc = tool_q.get()
        if tc is None:
            break
        calls.append(tc)

    summary = "".join(tokens).strip()
    tool_calls = []
    for tc in calls:
        name = tc.get("name", "")
        try:
            args = json.loads(tc.get("arguments", "{}"))
        except Exception:
            args = {}
        tool_calls.append({"name": name, "arguments": args})

    return summary, tool_calls


