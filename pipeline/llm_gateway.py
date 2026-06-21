# Created: 2026-05-26
# Purpose: Multi-LLM provider router. Reads the active provider from data/llm_providers.json
#   and returns endpoint, headers, model, and API compatibility mode. Called by streaming.py.
# Dependencies: stdlib + pipeline/auth/chatgpt.py (for ChatGPT OAuth path)

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pipeline.data_paths import llm_providers_path as _llm_providers_path, tool_groups_path as _tool_groups_path, repo_data_dir as _repo_data_dir
_PROVIDERS_PATH = _llm_providers_path()
_TOOL_GROUPS_PATH = _tool_groups_path()
_REPO_PROVIDERS_PATH = _repo_data_dir() / "llm_providers.json"


# Tool name prefix → group mapping
_TOOL_GROUP_RULES = [
    ("gmail_",                                                "gmail"),
    ("calendar_",                                             "calendar"),
    ("drive_",                                                "drive"),
    ("icloud_",                                               "icloud"),
    ("imessage_", "contacts_",                                "messages"),
    ("things_",                                               "things"),
    ("kis_",                                                  "kis"),
    ("xlsx_", "docx_", "pptx_",                               "office"),
    ("memory_", "persona_", "event_", "entity_",              "memory"),
    ("mcp_",                                                  "mcp_admin"),
    ("slack_",                                                "slack"),
    ("superthread_",                                          "superthread"),
    ("host_", "bash_", "python_", "sandbox_",                 "code"),
    ("web_",                                                  "web"),
    ("image_",                                                "image"),
    ("file_", "skill_", "widget_", "session_", "vega_",       "system"),
    ("ask_user_question", "exit_plan_mode",                   "system"),
]


def _tool_group_of(name: str) -> str:
    for *prefixes, group in _TOOL_GROUP_RULES:
        if any(name.startswith(p) for p in prefixes):
            return group
    return "misc"


def get_enabled_groups() -> set[str]:
    """Returns the set of enabled tool groups. If the file is missing, all groups are active (legacy behavior).

    파일 저장 이후에 새로 도입된 그룹("known"에 없는 그룹)은 기본 활성 —
    업데이트로 추가된 도구(slack/superthread 등)가 기존 사용자에게
    보이지 않는 문제를 막는다. 사용자가 명시적으로 끈 그룹은 유지."""
    if not _TOOL_GROUPS_PATH.exists():
        return _ALL_GROUPS
    try:
        data = json.loads(_TOOL_GROUPS_PATH.read_text(encoding="utf-8"))
        enabled = data.get("enabled")
        if not isinstance(enabled, list):
            return _ALL_GROUPS
        known = set(data.get("known") or _LEGACY_KNOWN_GROUPS)
        return set(enabled) | (_ALL_GROUPS - known)
    except Exception:
        return _ALL_GROUPS


def set_enabled_groups(groups: list[str]) -> None:
    _TOOL_GROUPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TOOL_GROUPS_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"enabled": list(groups), "known": sorted(_ALL_GROUPS)},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(_TOOL_GROUPS_PATH)


def filter_tools(schemas: list[dict]) -> list[dict]:
    """Returns only the tools that belong to an enabled group."""
    enabled = get_enabled_groups()
    return [s for s in schemas if _tool_group_of(s.get("name", "")) in enabled]


def tool_group_stats(schemas: list[dict]) -> list[dict]:
    """For UI: per-group tool count, enabled state, and estimated token usage."""
    enabled = get_enabled_groups()
    by_group: dict[str, list[dict]] = {}
    for s in schemas:
        by_group.setdefault(_tool_group_of(s.get("name", "")), []).append(s)
    from pipeline.token_count import count_json_tokens
    out = []
    for g, items in by_group.items():
        out.append({
            "group": g,
            "count": len(items),
            "tokens": count_json_tokens(items),
            "enabled": g in enabled,
            "names": [s.get("name") for s in items],
        })
    out.sort(key=lambda x: -x["count"])
    return out


_ALL_GROUPS = {
    "gmail", "calendar", "drive", "icloud", "messages", "things",
    "kis", "office", "memory", "mcp_admin", "code", "web", "image", "system", "misc",
    "slack", "superthread",
}

# "known" 필드 도입(2026-06-11, INT-1456) 이전에 저장된 파일이 알던 그룹 집합 —
# 이 집합에 없는 그룹은 구버전 파일에서 "사용자가 껐다"가 아니라 "몰랐다"로 해석한다.
_LEGACY_KNOWN_GROUPS = _ALL_GROUPS - {"slack", "superthread"}


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _read_config() -> dict:
    """Reads config fresh from disk on every call (hot-reload).
    Priority: user data directory → repo data/ → hardcoded defaults."""
    for path in (_PROVIDERS_PATH, _REPO_PROVIDERS_PATH):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return _default_config()


def _write_config(data: dict) -> None:
    data.setdefault("providers", {})
    tmp = _PROVIDERS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_PROVIDERS_PATH)


def _default_config() -> dict:
    return {
        "active": "chatgpt",
        "providers": {
            "chatgpt": {
                "label": "ChatGPT (Codex)",
                "kind": "responses",
                "auth_type": "chatgpt_oauth",
                "base_url": "https://chatgpt.com/backend-api/codex/responses",
                "default_model": "gpt-5.5",
                "extra_headers": {
                    "originator": "vega",
                    "OpenAI-Beta": "responses=experimental",
                },
            }
        },
    }


def get_active_name() -> str:
    return _read_config().get("active") or "chatgpt"


def get_active_provider() -> dict:
    """Returns the currently active provider dict (with key 'name' injected)."""
    cfg = _read_config()
    name = cfg.get("active") or "chatgpt"
    providers = cfg.get("providers") or {}
    prov = providers.get(name)
    if not prov:
        # fallback to chatgpt
        prov = providers.get("chatgpt") or _default_config()["providers"]["chatgpt"]
        name = "chatgpt"
    out = dict(prov)
    out["name"] = name
    return _expand_env(out)


# ── 2단 tier 라우팅 ────────────────────────────────────────────────────────────
# tier="local"  → 도메인 지식 질의/갱신 (결정론적 조회, SLM 으로 충분, 비용 0)
# tier="cloud"  → 즉각 업무지원 (문서 생성·웹 검색·추론, 로컬 SLM 은 품질/TTFT 한계)
# local provider 가 응답 없으면 cloud 로 자동 폴백 (로컬은 항상 다운 가능 전제).

def _provider_by_name(name: str) -> dict | None:
    cfg = _read_config()
    prov = (cfg.get("providers") or {}).get(name)
    if not prov:
        return None
    out = dict(prov)
    out["name"] = name
    return _expand_env(out)


def _is_provider_alive(prov: dict, timeout: float = 2.0) -> bool:
    """로컬 provider 생존 확인 (GET /models). 클라우드(bearer/oauth)는 항상 살아있다고 간주."""
    if prov.get("auth_type") != "none":
        return True  # 클라우드는 키만 있으면 가용으로 본다 (네트워크 체크 생략)
    base = prov.get("base_url", "")
    if not base:
        return False
    import urllib.request
    try:
        req = urllib.request.Request(base.rstrip("/") + "/models", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def _has_usable_key(prov: dict) -> bool:
    """bearer 프로바이더에 실제 키(env 또는 Keychain)가 있는지. OAuth/none(로컬)은
    키 개념이 다르므로 항상 True(가용으로 간주). build_request 의 키 해석과 동일 경로."""
    if prov.get("auth_type") != "bearer":
        return True
    key_env = prov.get("api_key_env", "")
    if not key_env:
        return False
    if os.getenv(key_env):
        return True
    try:
        from pipeline import keychain
        return bool(keychain.get_secret(key_env))
    except Exception:
        return False


def get_provider_for_tier(tier: str = "cloud") -> dict:
    """tier("local"|"cloud")에 매핑된 provider 를 반환. local 다운 시 cloud 폴백.

    tiers 매핑은 llm_providers.json 의 "tiers" 에서 읽는다. 없으면 active 단일 폴백.
    cloud tier 가 키 없는 bearer 프로바이더를 가리키면 active 로 폴백한다 — 설정/온보딩
    경로에서 active 와 tiers.cloud 가 어긋나도(키 없는 openrouter 등) 런타임 실패 대신
    동작하는 active 로 라우팅되는 read-time 안전망. (키가 있으면 의도적 분리로 보고 보존.)
    """
    cfg = _read_config()
    tiers = cfg.get("tiers") or {}
    name = tiers.get(tier)
    if not name:
        return get_active_provider()  # tier 미설정 → 기존 단일 동작

    prov = _provider_by_name(name)
    if not prov:
        return get_active_provider()

    # local tier 인데 SLM 이 죽어있으면 cloud 로 승급
    if tier == "local" and not _is_provider_alive(prov):
        cloud_name = tiers.get("cloud")
        cloud = _provider_by_name(cloud_name) if cloud_name else None
        if cloud:
            cloud = dict(cloud)
            cloud["_fell_back_from"] = "local"
            return cloud

    # cloud tier 가 키 없는 bearer 를 가리키면(divergence) active 로 폴백 — 단 active 가
    # 그 자신이면 무한 회피(이미 키 없음이 확정이므로 그대로 반환해 명시적 에러 유도).
    if tier == "cloud" and not _has_usable_key(prov):
        active = get_active_provider()
        if active.get("name") != prov.get("name") and _has_usable_key(active):
            active = dict(active)
            active["_fell_back_from"] = "cloud-keyless"
            return active

    return prov


def list_providers() -> list[dict]:
    """For UI — registered provider list with active flag and API key status."""
    cfg = _read_config()
    active = cfg.get("active") or "chatgpt"
    out: list[dict] = []
    for name, prov in (cfg.get("providers") or {}).items():
        auth_type = prov.get("auth_type", "none")
        key_env = prov.get("api_key_env", "")
        has_key = True if auth_type == "none" else (
            bool(os.getenv(key_env)) if auth_type == "bearer" else None  # OAuth: checked separately
        )
        out.append({
            "name": name,
            "label": prov.get("label", name),
            "kind": prov.get("kind", "chat_completions"),
            "auth_type": auth_type,
            "base_url": prov.get("base_url", ""),
            "default_model": prov.get("default_model", ""),
            "api_key_env": key_env,
            "has_key": has_key,
            "active": name == active,
            "reasoning_effort": prov.get("reasoning_effort") or None,
        })
    return out


def _is_local_provider(prov: dict) -> bool:
    """로컬(온디바이스) 프로바이더인지 — cloud tier 매핑 대상에서 제외하기 위함."""
    if (prov.get("auth_type") or "") == "none":
        return True
    base = (prov.get("base_url") or "").lower()
    return "localhost" in base or "127.0.0.1" in base


def set_active(name: str, sync_cloud_tier: bool = False) -> None:
    """활성 프로바이더 설정. sync_cloud_tier=True 면 클라우드 계열일 때
    tiers.cloud 도 같은 프로바이더로 맞춘다(온보딩에서 사용 — 첫 연결=메인).
    그렇지 않으면 active 와 tiers.cloud 가 어긋나 tier='cloud' 채팅이 엉뚱한
    프로바이더(예: 키 없는 openrouter)로 라우팅돼 실패한다."""
    cfg = _read_config()
    providers = cfg.get("providers") or {}
    if name not in providers:
        raise ValueError(f"unknown provider: {name}")
    cfg["active"] = name
    if sync_cloud_tier and not _is_local_provider(providers[name]):
        # setdefault 는 "tiers": null(present-but-None) 을 못 막는다 — reader(get_provider_for_tier)와
        # 같은 `or {}` 방어로 None 일 때 새 dict 로 교체한 뒤 대입한다.
        tiers = cfg.get("tiers")
        if not isinstance(tiers, dict):
            tiers = cfg["tiers"] = {}
        tiers["cloud"] = name
    _write_config(cfg)


def upsert_provider(name: str, entry: dict) -> None:
    cfg = _read_config()
    cfg.setdefault("providers", {})[name] = entry
    _write_config(cfg)


def remove_provider(name: str) -> None:
    cfg = _read_config()
    providers = cfg.get("providers") or {}
    if name not in providers:
        raise KeyError(name)
    del providers[name]
    # if the active provider is deleted, fall back to chatgpt
    if cfg.get("active") == name:
        cfg["active"] = "chatgpt" if "chatgpt" in providers else (next(iter(providers), "chatgpt"))
    _write_config(cfg)


def update_model(name: str, model: str) -> None:
    cfg = _read_config()
    providers = cfg.get("providers") or {}
    if name not in providers:
        raise KeyError(name)
    providers[name]["default_model"] = model
    _write_config(cfg)


_VALID_REASONING_EFFORTS = ("low", "medium", "high")

def update_reasoning_effort(name: str, effort: str | None) -> None:
    """chatgpt 등 responses kind 프로바이더의 reasoning_effort 업데이트.
    effort=None 또는 빈 문자열이면 필드 제거 (기본값 위임).
    유효값: 'low' | 'medium' | 'high'."""
    cfg = _read_config()
    providers = cfg.get("providers") or {}
    if name not in providers:
        raise KeyError(name)
    if effort:
        if effort not in _VALID_REASONING_EFFORTS:
            raise ValueError(f"reasoning_effort는 {_VALID_REASONING_EFFORTS} 중 하나여야 합니다: {effort!r}")
        # reasoning_effort는 responses kind(build_request)에서만 payload에 반영된다.
        # 다른 kind에 저장하면 조용히 무시되므로 저장 자체를 거부한다. (제거는 항상 허용)
        kind = providers[name].get("kind", "chat_completions")
        if kind != "responses":
            raise ValueError(f"reasoning_effort는 responses kind 프로바이더만 지원합니다 (kind={kind!r})")
        providers[name]["reasoning_effort"] = effort
    else:
        providers[name].pop("reasoning_effort", None)
    _write_config(cfg)


# ── Request building ─────────────────────────────────────────────────────────

# 이미지가 포함된 턴에서 활성 모델이 비전 미지원일 때 쓸 프로바이더별 비전 모델.
# 사용자 llm_providers.json 의 "vision_model" 필드가 우선하고, 없으면 이 맵을 쓴다.
# openrouter: deepseek-v4-flash(기본)가 이미지 입력 미지원 → 404 "No endpoints found
# that support image input" (INT-1466). gemini-3.1-flash-lite 는 OR /models 실측으로
# 비전 지원·최저가군 확인 + OCR 라이브 검증 완료.
_VISION_MODEL_FALLBACK = {
    "openrouter": "google/gemini-3.1-flash-lite",
}


def _has_image_input(input_items: list) -> bool:
    """input_items 에 이미지 블록(input_image/image)이 하나라도 있는지."""
    for item in input_items:
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") in ("input_image", "image"):
                    return True
    return False


def build_request(input_items: list, system: str, tool_schemas: list[dict], research_mode: bool = False, tier: str | None = None):
    """Builds a urllib.request.Request for the active provider (or the given tier).
    Replaces _build_request in streaming.py. Returns a (Request, kind) tuple.
    kind is 'responses' | 'chat_completions' — used to branch SSE parsing.

    tool_schemas is automatically filtered to the active tool groups.
    When research_mode=True, max_tokens/max_completion_tokens is set higher.
    tier ("local"|"cloud"): 지정 시 2단 라우터로 provider 선택(local 다운→cloud 폴백).
    None 이면 기존처럼 active provider 사용."""
    import urllib.request
    tool_schemas = filter_tools(tool_schemas)
    prov = get_provider_for_tier(tier) if tier else get_active_provider()
    kind = prov.get("kind", "chat_completions")
    model = prov.get("default_model") or ""
    # 이미지 포함 턴 — 비전 모델로 스위치 (기본 모델이 이미지 미지원인 프로바이더용, INT-1466)
    if _has_image_input(input_items):
        vision_model = prov.get("vision_model") or _VISION_MODEL_FALLBACK.get(prov.get("name", ""))
        if vision_model:
            model = vision_model
    auth_type = prov.get("auth_type", "none")
    base_url = prov.get("base_url", "")
    extra_headers = dict(prov.get("extra_headers") or {})

    # Auth headers + endpoint
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    headers.update(extra_headers)

    if auth_type == "chatgpt_oauth":
        from pipeline.auth.chatgpt import _load_profile, ensure_valid_token
        profile = _load_profile()
        if not profile:
            raise RuntimeError("ChatGPT OAuth 프로파일 없음")
        token = ensure_valid_token()
        headers["Authorization"] = f"Bearer {token}"
        headers["chatgpt-account-id"] = profile.get("account_id", "")
    elif auth_type == "claude_oauth":
        # Claude Code OAuth (Anthropic PKCE) — client_id/엔드포인트 비공개라 미구현(보류).
        # 값 확정 시 auth/claude.py 추가 + 아래 import 가드 해제.
        try:
            from pipeline.auth.claude import ensure_valid_token as _claude_token
        except ImportError:
            raise RuntimeError(
                "Claude Code OAuth 는 아직 미지원입니다. Anthropic API 키(anthropic_key) "
                "프로바이더를 쓰거나 설치 마법사에서 다른 프로바이더를 선택하세요."
            )
        token = _claude_token()
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("anthropic-version", "2023-06-01")
    elif auth_type == "anthropic_key":
        # Anthropic 직접 API (x-api-key). 콘솔 발급 키.
        key_env = prov.get("api_key_env", "") or "ANTHROPIC_API_KEY"
        key = os.getenv(key_env, "")
        if not key:
            from pipeline import keychain
            key = keychain.get_secret(key_env) or ""
        if not key:
            raise RuntimeError(
                f"{prov['name']}: API 키가 설정되지 않았습니다. "
                f"설정 창의 'AI 프로바이더'에서 {prov['name']} 키를 입력하세요."
            )
        headers["x-api-key"] = key
        headers.setdefault("anthropic-version", "2023-06-01")
    elif auth_type == "bearer":
        key_env = prov.get("api_key_env", "")
        key = os.getenv(key_env, "") if key_env else ""
        if not key and key_env:
            # 배포본은 키를 Keychain(서비스 VEGA)에 저장한다 — 환경변수만 보면
            # 프로세스 재시작 후 키를 못 찾는다. anthropic_key 분기와 동일하게 폴백.
            from pipeline import keychain
            key = keychain.get_secret(key_env) or ""
        if not key:
            raise RuntimeError(
                f"{prov['name']}: API 키가 설정되지 않았습니다. "
                f"설정 창의 'AI 프로바이더'에서 {prov['name']} 키를 입력하세요."
            )
        headers["Authorization"] = f"Bearer {key}"
    # auth_type == "none" → no additional headers (local provider)

    # Endpoint URL + payload
    # Research mode: allow a generous response token limit.
    # Note: ChatGPT Codex Responses API rejects max_output_tokens (HTTP 400).
    # Omit the field entirely outside research_mode; add it only for non-ChatGPT providers in research_mode.
    _res_max = 16000 if research_mode else 8000
    _is_chatgpt = "chatgpt.com" in base_url or auth_type == "chatgpt_oauth"

    if kind == "responses":
        url = base_url  # already points to .../responses
        payload = {
            "model": model,
            "instructions": system,
            "input": input_items,
            "store": False,
            "stream": True,
            "tools": tool_schemas,
        }
        # Only set token limit when not ChatGPT Codex and in research mode
        if research_mode and not _is_chatgpt:
            payload["max_output_tokens"] = _res_max
        # reasoning_effort: 프로바이더 설정값 우선, research_mode면 "high" 폴백
        _effort = prov.get("reasoning_effort") or ("high" if research_mode else None)
        if _effort:
            payload["reasoning"] = {"effort": _effort, "summary": "auto"}
    elif kind == "anthropic":
        # Anthropic Messages API (/v1/messages)
        url = base_url.rstrip("/") + "/messages"
        messages = _responses_to_anthropic_messages(input_items)
        an_tools = _to_anthropic_tools(tool_schemas)
        # system: 캐시 마커 적용 위해 블록 배열로. tools 가 있으면 마지막 tool 에도 마커.
        system_blocks = [{"type": "text", "text": system or ""}]
        if system_blocks[0]["text"]:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}
        if an_tools:
            an_tools[-1]["cache_control"] = {"type": "ephemeral"}
        payload = {
            "model": model,
            "system": system_blocks,
            "messages": messages,
            "max_tokens": 16000 if research_mode else 8000,
            "stream": True,
        }
        if an_tools:
            payload["tools"] = an_tools
    else:  # chat_completions
        url = base_url.rstrip("/") + "/chat/completions"
        messages = _responses_to_messages(system, input_items)
        cc_tools = _to_chat_completions_tools(tool_schemas)

        # Prompt caching:
        # - Anthropic models: must explicitly set cache_control:{type:'ephemeral'} at end of system + tools
        # - OpenAI/DeepSeek/Gemini/Grok: OpenRouter caches automatically (markers not needed; ignored if sent)
        # Sending markers to all models is the simplest and safest approach.
        # OpenRouter docs: https://openrouter.ai/docs/features/prompt-caching
        _apply_cache_markers(messages, cc_tools)

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "usage": {"include": True},  # OpenRouter: include usage in stream
            "stream_options": {"include_usage": True},  # OpenAI-compat (mlx-server, etc.): usage in last chunk
        }
        # Only set max_tokens in research_mode — otherwise use provider default
        if research_mode:
            payload["max_tokens"] = _res_max
        if cc_tools:
            payload["tools"] = cc_tools
            payload["tool_choice"] = "auto"

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    return req, kind


def _responses_to_messages(system: str, input_items: list[dict]) -> list[dict]:
    """Converts Responses API input (varied roles/types) → ChatCompletions messages.
    MVP: maps text + function_call_output only. Images converted to OpenAI vision format."""
    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    for item in input_items:
        itype = item.get("type")
        if itype == "function_call_output":
            msgs.append({
                "role": "tool",
                "tool_call_id": item.get("call_id", ""),
                "content": item.get("output", ""),
            })
            continue
        if itype == "function_call":
            # Assistant-side tool call record
            msgs.append({
                "role": "assistant",
                "tool_calls": [{
                    "id": item.get("call_id", ""),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", "{}"),
                    },
                }],
            })
            continue
        # message type
        role = item.get("role", "user")
        content = item.get("content")
        if isinstance(content, list):
            # multimodal — Responses {type:input_text|input_image} → ChatCompletions {type:text|image_url}
            new_content = []
            for c in content:
                ct = c.get("type")
                if ct in ("input_text", "text"):
                    new_content.append({"type": "text", "text": c.get("text", "")})
                elif ct in ("input_image", "image"):
                    url = c.get("image_url") or c.get("url") or c.get("image")
                    if isinstance(url, dict):
                        url = url.get("url", "")
                    new_content.append({"type": "image_url", "image_url": {"url": url}})
            msgs.append({"role": role, "content": new_content})
        else:
            msgs.append({"role": role, "content": content or ""})
    return msgs


def _responses_to_anthropic_messages(input_items: list[dict]) -> list[dict]:
    """Responses API input → Anthropic Messages 포맷.

    - message(text/image) → {role, content:[{type:text|image}]}
    - function_call        → assistant {content:[{type:tool_use, id, name, input}]}
    - function_call_output → user {content:[{type:tool_result, tool_use_id, content}]}
    Anthropic 은 연속 동일 role 을 허용하므로 그대로 매핑한다."""
    msgs: list[dict] = []
    for item in input_items:
        itype = item.get("type")
        if itype == "function_call":
            try:
                inp = json.loads(item.get("arguments") or "{}")
            except Exception:
                inp = {}
            msgs.append({"role": "assistant", "content": [{
                "type": "tool_use",
                "id": item.get("call_id", ""),
                "name": item.get("name", ""),
                "input": inp,
            }]})
            continue
        if itype == "function_call_output":
            msgs.append({"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": item.get("call_id", ""),
                "content": item.get("output", ""),
            }]})
            continue
        # message
        role = item.get("role", "user")
        if role not in ("user", "assistant"):
            role = "user"
        content = item.get("content")
        if isinstance(content, list):
            blocks = []
            for c in content:
                ct = c.get("type")
                if ct in ("input_text", "text"):
                    blocks.append({"type": "text", "text": c.get("text", "")})
                elif ct in ("input_image", "image"):
                    url = c.get("image_url") or c.get("url") or c.get("image")
                    if isinstance(url, dict):
                        url = url.get("url", "")
                    # data URI → base64 source; http URL → url source
                    if isinstance(url, str) and url.startswith("data:"):
                        try:
                            meta, b64 = url.split(",", 1)
                            media = meta.split(";")[0].split(":", 1)[1]
                            blocks.append({"type": "image", "source": {
                                "type": "base64", "media_type": media, "data": b64}})
                        except Exception:
                            pass
                    elif isinstance(url, str) and url:
                        blocks.append({"type": "image", "source": {"type": "url", "url": url}})
            msgs.append({"role": role, "content": blocks})
        else:
            msgs.append({"role": role, "content": content or ""})
    # Anthropic 은 첫 메시지가 user 여야 함 — assistant 로 시작하면 빈 user 삽입
    if msgs and msgs[0]["role"] == "assistant":
        msgs.insert(0, {"role": "user", "content": "."})
    return msgs


def _to_anthropic_tools(schemas: list[dict]) -> list[dict]:
    """OpenAI Responses tool schema → Anthropic tool schema.
    Responses: {type, name, description, parameters}
    Anthropic: {name, description, input_schema}"""
    out = []
    for s in schemas:
        if s.get("type") != "function":
            continue
        out.append({
            "name": s.get("name", ""),
            "description": s.get("description", ""),
            "input_schema": s.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _apply_cache_markers(messages: list[dict], tools: list[dict]) -> None:
    """Adds OpenRouter prompt caching markers in-place.

    Inserts `cache_control: {"type": "ephemeral"}` (for Anthropic models) at:
    1. The last element of the system message content (or the system message itself)
    2. The last tool in the tools array

    OpenAI/DeepSeek/Gemini/Grok: OpenRouter caches automatically, markers are ignored.
    Anthropic models require explicit markers to create a 5-minute ephemeral cache.
    """
    # 1. system message
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str):
                # Convert string → cacheable multipart format
                msg["content"] = [{
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }]
            elif isinstance(content, list) and content:
                # Already multipart: attach marker to the last text block
                for block in reversed(content):
                    if isinstance(block, dict) and block.get("type") == "text":
                        block["cache_control"] = {"type": "ephemeral"}
                        break
            break  # system message is normally only one

    # 2. Last tool entry
    if tools:
        last = tools[-1]
        if isinstance(last, dict):
            last["cache_control"] = {"type": "ephemeral"}


def _to_chat_completions_tools(schemas: list[dict]) -> list[dict]:
    """Converts OpenAI Responses tool schema → ChatCompletions tool schema."""
    out = []
    for s in schemas:
        if s.get("type") != "function":
            continue
        # Responses: {type, name, description, parameters}
        # CC:        {type:'function', function:{name, description, parameters}}
        out.append({
            "type": "function",
            "function": {
                "name": s.get("name", ""),
                "description": s.get("description", ""),
                "parameters": s.get("parameters") or {"type": "object", "properties": {}},
            },
        })
    return out


# ── Connection test ──────────────────────────────────────────────────────────

def test_provider(name: str) -> dict:
    """Tests provider connectivity with a lightweight call. Tries GET /models or base_url."""
    import urllib.request, urllib.error
    cfg = _read_config()
    prov = (cfg.get("providers") or {}).get(name)
    if not prov:
        return {"ok": False, "error": f"등록되지 않음: {name}"}
    prov = _expand_env(prov)
    auth_type = prov.get("auth_type", "none")
    base_url = prov.get("base_url", "")
    kind = prov.get("kind", "chat_completions")

    # Responses (ChatGPT): handled separately — only verify OAuth token issuance
    if kind == "responses":
        try:
            from pipeline.auth.chatgpt import _load_profile, ensure_valid_token
            if not _load_profile():
                return {"ok": False, "error": "ChatGPT OAuth 프로파일 없음"}
            tok = ensure_valid_token()
            return {"ok": bool(tok), "info": "OAuth 토큰 유효"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ChatCompletions-compatible → GET /models
    url = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if auth_type == "bearer":
        key_env = prov.get("api_key_env", "")
        key = os.getenv(key_env, "") if key_env else ""
        if not key and key_env:
            from pipeline import keychain
            key = keychain.get_secret(key_env) or ""
        if not key:
            return {"ok": False, "error": "API 키 미설정 — 설정 창에서 키를 입력하세요"}
        headers["Authorization"] = f"Bearer {key}"

    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        models = data.get("data") or data.get("models") or []
        n = len(models) if isinstance(models, list) else 0
        return {"ok": True, "model_count": n, "models": [m.get("id") for m in models[:6] if isinstance(m, dict)]}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"연결 실패: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
