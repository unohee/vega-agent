# Created: 2026-05-31
# Purpose: 채널 공통 코어. (1) 채널 대화 ID ↔ vega 세션 ID 매핑, (2) stream_gpt 를
#          채널 어댑터가 쓰기 좋은 형태(델타 콜백)로 감싼 run_agent_turn().
# Dependencies: pipeline.streaming, pipeline.session_store, pipeline.tools, pipeline.mcp_client
"""채널 어댑터 공통 코어.

run_agent_turn(channel, conv_id, user_text, on_delta) 한 함수로
- 채널 대화 ID 로 vega 세션을 찾거나 만들고
- 직전 히스토리를 복원해 stream_gpt 를 돌리고
- 토큰이 쌓일 때마다 on_delta(누적텍스트) 를 호출(점진 편집용)
- 최종 답변 전체를 반환하고 세션에 user/assistant 메시지를 저장한다.

채널 어댑터는 on_delta 안에서 자기 방식(텔레그램 edit_message_text, 슬랙 chat_update)으로
화면을 갱신하기만 하면 된다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable

from pipeline import session_store
from pipeline import streaming

# ── MCP 도구 1회 주입 ────────────────────────────────────────────────────────
# server.py lifespan 과 동일하게, 채널 단독 실행 시에도 kyte 등 MCP 도구를
# TOOL_SCHEMAS 에 합쳐 둔다. (프로세스당 1회)
_mcp_ready = False


async def ensure_mcp_loaded() -> None:
    global _mcp_ready
    if _mcp_ready:
        return
    try:
        from pipeline import mcp_client, tools as tools_mod
        schemas = await mcp_client.init_mcp_tools()
        existing = {s.get("name") for s in tools_mod.TOOL_SCHEMAS}
        for _srv, ts in schemas.items():
            for s in ts:
                if s.get("name") not in existing:
                    tools_mod.TOOL_SCHEMAS.append(s)
    except Exception as e:  # MCP 없어도 에이전트는 동작해야 함
        print(f"[channels] MCP load warning: {e}")
    _mcp_ready = True


# ── 채널 대화 ID ↔ vega 세션 ID 매핑 ─────────────────────────────────────────
# data/channel_sessions.json 에 {"telegram:12345": "vega-session-uuid", ...} 로 영속.
def _map_path() -> Path:
    from pipeline.data_paths import repo_data_dir
    return repo_data_dir() / "channel_sessions.json"


def _load_map() -> dict[str, str]:
    p = _map_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_map(m: dict[str, str]) -> None:
    p = _map_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def session_for(channel: str, conv_id: str, *, title: str = "") -> str:
    """채널 대화 키(channel:conv_id)에 대응하는 vega 세션 ID 를 반환(없으면 생성)."""
    key = f"{channel}:{conv_id}"
    m = _load_map()
    sid = m.get(key)
    if sid and session_store.get_session(sid):
        return sid
    sid = session_store.create_session(title=title or key)
    m[key] = sid
    _save_map(m)
    return sid


def reset_session(channel: str, conv_id: str) -> str:
    """대화 새로 시작 — 새 세션을 만들어 매핑을 교체하고 새 세션 ID 반환."""
    key = f"{channel}:{conv_id}"
    m = _load_map()
    sid = session_store.create_session(title=key)
    m[key] = sid
    _save_map(m)
    return sid


# ── 에이전트 1턴 실행 ────────────────────────────────────────────────────────
def _kyte_tool_hint() -> str:
    """KYTE 도구 디스커버리 힌트. 모델이 회사 데이터 질문에 web_search 대신
    kyte__ MCP 도구를 쓰도록 system 프롬프트에 덧붙인다. kyte 도구가 로드되지 않았으면 빈 문자열."""
    try:
        from pipeline import tools as _t
        kyte_names = sorted(
            s.get("name", "") for s in _t.TOOL_SCHEMAS
            if str(s.get("name", "")).startswith("kyte__")
        )
    except Exception:
        kyte_names = []
    if not kyte_names:
        return ""
    return (
        "\n\n## KYTE 회사 데이터 — 반드시 kyte 도구로 조회\n"
        "회사(KYTE) 데이터 질문(작품·정산·OKR·메일·일정·카드 등)은 절대 추측하거나 웹 검색하지 말고 "
        "아래 kyte__ 도구로만 조회한다. 'k1', 'k182' 같은 식별자는 KYTE Work ID 이며 "
        "`kyte__find_work`(kyte_work_id 인자)로 단건 조회한다.\n"
        f"가용 도구: {', '.join(kyte_names)}\n"
    )


async def run_agent_turn(
    channel: str,
    conv_id: str,
    user_text: str,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    *,
    ce_mode: bool = True,
    title: str = "",
) -> str:
    """채널 메시지 1건을 에이전트로 처리하고 최종 답변 텍스트를 반환.

    on_delta(누적_텍스트): 토큰이 들어올 때마다 호출(점진 편집용). None 이면 최종만.
    ce_mode: 원격 채널이므로 기본 True — CE allowlist 도구만 노출(로컬 파일/exec 차단).
    """
    await ensure_mcp_loaded()

    sid = session_for(channel, conv_id, title=title)
    history = session_store.load_history(sid)  # [{"role","content"}, ...]
    messages = history + [{"role": "user", "content": user_text}]

    system = streaming.build_system() + _kyte_tool_hint()

    # 2단 라우팅: 도메인 질의/갱신 → local SLM, 즉각 지원(생성·추론·검색) → cloud.
    # local 다운 시 llm_gateway.get_provider_for_tier 가 cloud 로 자동 폴백.
    from pipeline.tier_router import route_tier
    tier = route_tier(user_text, history)

    acc = {"text": ""}

    async def _on_token(tok: str) -> None:
        acc["text"] += tok
        if on_delta is not None:
            await on_delta(acc["text"])

    final = await streaming.stream_gpt(
        messages=messages,
        system=system,
        tier=tier,
        on_token=_on_token,
        ce_mode=ce_mode,
    )

    # 세션 영속 (human → assistant 순).
    # 주의: session_store.load_history 는 sender=="human" 만 user 로 매핑하므로
    # 반드시 "human" 으로 저장해야 다음 턴 히스토리에서 역할이 보존된다.
    session_store.append_message(sid, "human", user_text)
    session_store.append_message(sid, "assistant", final or acc["text"])
    return final or acc["text"]
