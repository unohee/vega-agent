# Created: 2026-05-27
# Purpose: LLM provider + MCP server management + model catalog endpoints
# Previously in: web/server.py (lines 1078-1408)

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

def _agent_md_dir() -> Path:
    try:
        from pipeline.data_paths import data_dir
        return data_dir() / "agents"
    except Exception:
        return Path(__file__).parent.parent.parent / "data" / "agents"

def _mcp_json_path() -> Path:
    try:
        from pipeline.data_paths import data_dir
        return data_dir() / "mcp.json"
    except Exception:
        return Path(__file__).parent.parent.parent / "data" / "mcp.json"

# ── Per-provider model catalog (5-minute cache) ───────────────────────────────
_MODELS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_MODELS_CACHE_TTL = 300

import re as _re

def _parse_params_b(model_id: str) -> float | None:
    """Extract parameter count (billions) from a model ID. e.g. llama-3-70b→70, mixtral-8x7b→56."""
    s = model_id.lower()
    # MoE: NxMb pattern (e.g. 8x7b → 56B, 8x22b → 176B)
    m = _re.search(r'(\d+)x(\d+\.?\d*)b', s)
    if m:
        return float(m.group(1)) * float(m.group(2))
    # Standard: Nb pattern (e.g. 70b, 7b, 405b, 1.5b)
    m = _re.search(r'(\d+\.?\d*)b(?:\b|[-_])', s)
    if m:
        return float(m.group(1))
    return None

_MCP_ALLOWED_COMMANDS = {"npx", "uvx", "python", "python3", "node", "deno", "bun"}


# ── LLM providers ────────────────────────────────────────────────────────────

@router.get("/api/llm/providers")
async def llm_providers_list():
    from pipeline.llm_gateway import list_providers, get_active_name
    return JSONResponse({"providers": list_providers(), "active": get_active_name()})


@router.post("/api/llm/active")
async def llm_set_active(request: Request):
    from pipeline.llm_gateway import set_active
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name 필수"}, status_code=400)
    try:
        # 온보딩과 동일하게 cloud tier 도 맞춘다 — 설정에서 active 를 바꿨는데 tiers.cloud 가
        # 옛 프로바이더에 남으면 tier='cloud' 채팅이 엉뚱한(키 없는) 곳으로 라우팅된다.
        set_active(name, sync_cloud_tier=True)
        return JSONResponse({"ok": True, "active": name})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/llm/model")
async def llm_update_model(request: Request):
    """Update the default_model of the currently active provider. body: {name, model}"""
    from pipeline.llm_gateway import update_model
    body = await request.json()
    name = (body.get("name") or "").strip()
    model = (body.get("model") or "").strip()
    if not name or not model:
        return JSONResponse({"error": "name, model 필수"}, status_code=400)
    try:
        update_model(name, model)
        return JSONResponse({"ok": True})
    except KeyError:
        return JSONResponse({"error": f"unknown provider: {name}"}, status_code=404)


@router.post("/api/llm/reasoning")
async def llm_update_reasoning(request: Request):
    """responses kind 프로바이더의 reasoning_effort 업데이트.
    body: {name, effort}  — effort 빈 문자열이면 필드 제거."""
    from pipeline.llm_gateway import update_reasoning_effort
    body = await request.json()
    name = (body.get("name") or "").strip()
    effort = (body.get("effort") or "").strip()
    if not name:
        return JSONResponse({"error": "name 필수"}, status_code=400)
    try:
        update_reasoning_effort(name, effort or None)
        return JSONResponse({"ok": True, "effort": effort or None})
    except KeyError:
        return JSONResponse({"error": f"unknown provider: {name}"}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.post("/api/llm/test")
async def llm_test_provider(request: Request):
    from pipeline.llm_gateway import test_provider
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name 필수"}, status_code=400)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, test_provider, name)
    return JSONResponse(result)


# ── GPT OAuth 상태 / 재로그인 ────────────────────────────────────────────────

@router.get("/api/llm/gpt-auth")
async def gpt_auth_status():
    """ChatGPT OAuth 프로파일 상태 — UI 재로그인 버튼 표시 판단용.
    {ok: bool, remaining_min: int, account_id: str|None}"""
    import time as _t
    try:
        from pipeline.auth.chatgpt import _load_profile
        profile = _load_profile()
        if not profile:
            return JSONResponse({"ok": False, "reason": "프로파일 없음"})
        remains = profile.get("expires_at", 0) - int(_t.time())
        return JSONResponse({
            "ok": True,
            "remaining_min": max(0, remains // 60),
            "account_id": profile.get("account_id"),
            "has_refresh": bool(profile.get("refresh_token")),
        })
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e).split(chr(10))[0]})


@router.post("/api/llm/gpt-relogin")
async def gpt_relogin():
    """ChatGPT OAuth 재로그인 — 시스템 브라우저로 OpenAI 로그인 흐름을 띄운다.
    login()이 브라우저를 열고 로컬 콜백(포트 1455)으로 토큰을 받아 저장한다(블로킹).
    사용자가 브라우저에서 로그인을 마칠 때까지 대기(최대 ~120초)."""
    loop = asyncio.get_event_loop()
    try:
        from pipeline.auth.chatgpt import login
        profile = await loop.run_in_executor(None, login)
        return JSONResponse({
            "ok": True,
            "account_id": profile.get("account_id"),
            "message": "GPT OAuth 재로그인 완료",
        })
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": str(e).split(chr(10))[0]},
            status_code=500,
        )


# ── Model catalog ────────────────────────────────────────────────────────────

# ChatGPT(Codex) live discovery 실패 시 curated fallback.
# hermes_cli/codex_models.py DEFAULT_CODEX_MODELS 미러. gpt-5.3-codex-spark는
# supported_in_api=false지만 OAuth Codex 백엔드에선 유효 — 그 플래그로 필터하지 않는다.
_CODEX_FALLBACK_MODELS = ["gpt-5.5", "gpt-5.4-mini", "gpt-5.4", "gpt-5.3-codex", "gpt-5.3-codex-spark"]


def _fetch_codex_model_slugs(prov: dict) -> list[str]:
    """ChatGPT(Codex) OAuth 백엔드의 모델 슬러그를 live 조회.
    표준 OpenAI /models가 아니라 codex 전용 엔드포인트(/backend-api/codex/models)를 쓴다.
    토큰/네트워크 실패 시 빈 리스트 → 호출부가 fallback 카탈로그로 대체."""
    import urllib.request as _ur
    if prov.get("auth_type") != "chatgpt_oauth":
        return []
    try:
        from pipeline.auth.chatgpt import ensure_valid_token
        token = ensure_valid_token()
    except Exception:
        return []
    if not token:
        return []
    base = (prov.get("base_url") or "").rstrip("/")
    if base.endswith("/responses"):
        base = base[: -len("/responses")]
    url = base + "/models?client_version=1.0.0"
    try:
        req = _ur.Request(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            method="GET",
        )
        with _ur.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return []
    entries = data.get("models", []) if isinstance(data, dict) else []
    sortable: list[tuple[int, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        slug = slug.strip()
        # visibility=hide/hidden만 제외. supported_in_api는 공개 API용 플래그라 무시.
        vis = item.get("visibility")
        if isinstance(vis, str) and vis.strip().lower() in {"hide", "hidden"}:
            continue
        prio = item.get("priority")
        rank = int(prio) if isinstance(prio, (int, float)) else 10_000
        sortable.append((rank, slug))
    sortable.sort(key=lambda x: (x[0], x[1]))
    deduped: list[str] = []
    for _, slug in sortable:
        if slug not in deduped:
            deduped.append(slug)
    return deduped


def _fetch_models(provider_name: str) -> list[dict]:
    """Call the provider's /models endpoint and normalize the response. Uses cache."""
    import time as _t
    import urllib.request as _ur
    now = _t.time()
    cached = _MODELS_CACHE.get(provider_name)
    if cached and (now - cached[0]) < _MODELS_CACHE_TTL:
        return cached[1]

    from pipeline.llm_gateway import _read_config, _expand_env
    prov = (_read_config().get("providers") or {}).get(provider_name)
    if not prov:
        return []
    prov = _expand_env(prov)
    base_url = prov.get("base_url", "")
    kind = prov.get("kind", "chat_completions")
    auth_type = prov.get("auth_type", "none")
    if kind == "responses":
        slugs = _fetch_codex_model_slugs(prov) or list(_CODEX_FALLBACK_MODELS)
        default = prov.get("default_model", "gpt-5.5")
        if default and default not in slugs:
            slugs.insert(0, default)
        models = [{"id": s, "name": s, "context_length": None,
                   "modalities": ["text"], "pricing": None} for s in slugs]
        _MODELS_CACHE[provider_name] = (now, models)
        return models

    url = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if auth_type == "bearer":
        key_env = prov.get("api_key_env", "")
        key = os.getenv(key_env, "")
        if not key:
            try:
                from pipeline import keychain
                key = keychain.get_secret(key_env) or ""
            except Exception:
                pass
        if not key:
            return []
        headers["Authorization"] = f"Bearer {key}"
    try:
        req = _ur.Request(url, headers=headers, method="GET")
        with _ur.urlopen(req, timeout=10) as r:
            raw = json.loads(r.read().decode())
    except Exception:
        return []
    items = raw.get("data") or raw.get("models") or []
    out = []
    for m in items:
        if not isinstance(m, dict):
            continue
        mid = m.get("id") or m.get("model") or ""
        if not mid:
            continue
        arch = m.get("architecture") or {}
        modalities = arch.get("input_modalities") or (
            ["text", "image"] if "image" in (arch.get("modality") or "") else ["text"]
        )
        pricing = m.get("pricing") or {}
        try:
            p_in = float(pricing.get("prompt", 0)) * 1_000_000 if pricing else None
            p_out = float(pricing.get("completion", 0)) * 1_000_000 if pricing else None
        except Exception:
            p_in = p_out = None
        # Exclude free-tier endpoints (unusable due to rate limits)
        if p_in == 0 and p_out == 0:
            continue
        # Extract parameter count from model ID (e.g. llama-3-70b → 70, mixtral-8x7b → 56)
        num_params = _parse_params_b(mid)
        out.append({
            "id": mid,
            "name": m.get("name") or mid,
            "description": (m.get("description") or "")[:200],
            "context_length": m.get("context_length") or m.get("top_provider", {}).get("context_length"),
            "modalities": modalities,
            "price_in_per_mtok": p_in,
            "price_out_per_mtok": p_out,
            "num_params_b": num_params,
        })
    _MODELS_CACHE[provider_name] = (now, out)
    return out


def enrich_usage_stats(stats: dict) -> None:
    """Enrich usage_stats with pricing (in/out/total USD) and active provider info.
    Modifies in-place. Also imported by the chat router."""
    if not stats:
        return
    try:
        from pipeline.llm_gateway import get_active_provider
        prov = get_active_provider()
        stats["provider"] = prov.get("name", "")
        if not stats.get("model"):
            stats["model"] = prov.get("default_model", "")
        models = _fetch_models(prov.get("name", ""))
        model_id = stats.get("model", "")
        m = next((x for x in models if x.get("id") == model_id), None)
        if m:
            in_tok = stats.get("input_tokens", 0)
            out_tok = stats.get("output_tokens", 0)
            cached = stats.get("cached_tokens", 0)
            p_in = m.get("price_in_per_mtok") or 0
            p_out = m.get("price_out_per_mtok") or 0
            uncached_in = max(0, in_tok - cached)
            cost_in = (uncached_in / 1_000_000) * p_in + (cached / 1_000_000) * p_in * 0.1
            cost_out = (out_tok / 1_000_000) * p_out
            stats["cost_in_usd"] = round(cost_in, 6)
            stats["cost_out_usd"] = round(cost_out, 6)
            stats["cost_total_usd"] = round(cost_in + cost_out, 6)
            stats["price_in_per_mtok"] = p_in
            stats["price_out_per_mtok"] = p_out
    except Exception:
        pass


@router.get("/api/llm/models")
async def llm_models_list(provider: str = ""):
    """Available models for a provider. Defaults to the active provider if omitted."""
    from pipeline.llm_gateway import get_active_name
    name = provider or get_active_name()
    loop = asyncio.get_event_loop()
    models = await loop.run_in_executor(None, _fetch_models, name)
    return JSONResponse({"provider": name, "count": len(models), "models": models})


# ── Tool groups ───────────────────────────────────────────────────────────────

@router.get("/api/llm/tool-groups")
async def llm_tool_groups():
    """Per-group tool statistics and enabled state."""
    from pipeline.llm_gateway import tool_group_stats, get_enabled_groups
    from pipeline import tools as _tools
    stats = tool_group_stats(_tools.TOOL_SCHEMAS)
    return JSONResponse({
        "groups": stats,
        "enabled": sorted(get_enabled_groups()),
        "total_tools": len(_tools.TOOL_SCHEMAS),
        "active_tools": sum(g["count"] for g in stats if g["enabled"]),
        "active_tokens": sum(g["tokens"] for g in stats if g["enabled"]),
    })


@router.post("/api/llm/tool-groups")
async def llm_tool_groups_set(request: Request):
    from pipeline.llm_gateway import set_enabled_groups
    body = await request.json()
    groups = body.get("enabled")
    if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        return JSONResponse({"error": "enabled must be a string array"}, status_code=400)
    set_enabled_groups(groups)
    return JSONResponse({"ok": True, "enabled": groups})


# ── AGENT.md ─────────────────────────────────────────────────────────────────

@router.get("/api/llm/agent-md")
async def llm_agent_md_get(name: str):
    """Return data/agents/{name}.md. name='_default' is also valid. Returns empty string if file is absent."""
    if "/" in name or ".." in name:
        return JSONResponse({"error": "invalid name"}, status_code=400)
    from pipeline.data_paths import agent_md_path
    p = agent_md_path(name)
    if not p.exists():
        return JSONResponse({"name": name, "exists": False, "text": ""})
    try:
        return JSONResponse({"name": name, "exists": True, "text": p.read_text(encoding="utf-8")})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/llm/agent-md")
async def llm_agent_md_set(request: Request):
    """Save data/agents/{name}.md. body: {name, text}"""
    body = await request.json()
    name = (body.get("name") or "").strip()
    text = body.get("text", "")
    if not name or "/" in name or ".." in name:
        return JSONResponse({"error": "invalid name"}, status_code=400)
    if not isinstance(text, str):
        return JSONResponse({"error": "text must be string"}, status_code=400)
    if len(text) > 200_000:
        return JSONResponse({"error": "too large (>200KB)"}, status_code=400)
    _agent_md_dir().mkdir(parents=True, exist_ok=True)
    p = _agent_md_dir() / f"{name}.md"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)
    return JSONResponse({"ok": True})


# ── MCP server management ────────────────────────────────────────────────────

def _read_mcp_json() -> dict:
    if not _mcp_json_path().exists():
        return {"mcpServers": {}}
    try:
        return json.loads(_mcp_json_path().read_text(encoding="utf-8"))
    except Exception:
        return {"mcpServers": {}}


def _write_mcp_json(data: dict) -> None:
    if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
        data["mcpServers"] = {}
    _p = _mcp_json_path()
    _tmp = _p.with_suffix(".tmp")
    _tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _tmp.replace(_p)


def _validate_mcp_entry(name: str, entry: dict) -> str | None:
    """Validate an MCP entry. Returns an error message on failure, None on success."""
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        return "name may only contain alphanumeric characters, hyphens, and underscores"
    if not isinstance(entry, dict):
        return "entry must be an object"
    has_cmd = "command" in entry
    has_url = "url" in entry
    if not (has_cmd or has_url):
        return "either command or url is required"
    if has_cmd:
        cmd = entry.get("command", "")
        if not isinstance(cmd, str) or not cmd:
            return "command must be a non-empty string"
        if not (cmd in _MCP_ALLOWED_COMMANDS or cmd.startswith("/")):
            return f"command must be one of {sorted(_MCP_ALLOWED_COMMANDS)} or an absolute path"
        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            return "args must be a string array"
        env = entry.get("env", {})
        if env and not isinstance(env, dict):
            return "env must be an object"
    if has_url:
        url = entry.get("url", "")
        if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
            return "url must start with http(s)://"
        headers = entry.get("headers", {})
        if headers and not isinstance(headers, dict):
            return "headers must be an object"
    return None


@router.get("/api/mcp/servers")
async def mcp_list():
    """List registered MCP servers including auto-registered servers (e.g. linear via env var)."""
    from pipeline.mcp_client import _load_registry, _tool_cache  # type: ignore

    data = _read_mcp_json()
    explicit = data.get("mcpServers") or {}
    runtime_reg = _load_registry()

    out = []
    for name, cfg in runtime_reg.items():
        tools = _tool_cache.get(name, [])
        is_explicit = name in explicit
        out.append({
            "name": name,
            "transport": cfg.get("transport"),
            "command": cfg.get("command"),
            "args": cfg.get("args"),
            "url": cfg.get("url"),
            "has_headers": bool(cfg.get("headers")),
            "has_env": bool(cfg.get("env")),
            "auto": not is_explicit,
            "tool_count": len(tools),
            "raw": explicit.get(name) if is_explicit else None,
        })
    return JSONResponse({"servers": out})


@router.post("/api/mcp/servers")
async def mcp_create(request: Request):
    """Add a new MCP server. body: {name, entry: {command|url, ...}}"""
    body = await request.json()
    name = (body.get("name") or "").strip()
    entry = body.get("entry") or {}
    err = _validate_mcp_entry(name, entry)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    data = _read_mcp_json()
    data.setdefault("mcpServers", {})
    if name in data["mcpServers"]:
        return JSONResponse({"error": f"'{name}' already exists (use PUT to update)"}, status_code=409)
    data["mcpServers"][name] = entry
    _write_mcp_json(data)
    return JSONResponse({"ok": True, "name": name})


@router.put("/api/mcp/servers/{name}")
async def mcp_update(name: str, request: Request):
    """Update an existing server."""
    body = await request.json()
    entry = body.get("entry") or {}
    err = _validate_mcp_entry(name, entry)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    data = _read_mcp_json()
    data.setdefault("mcpServers", {})[name] = entry
    _write_mcp_json(data)
    return JSONResponse({"ok": True, "name": name})


@router.delete("/api/mcp/servers/{name}")
async def mcp_delete(name: str):
    """Remove a server from mcp.json only — env-based auto servers cannot be deleted here."""
    data = _read_mcp_json()
    servers = data.get("mcpServers") or {}
    if name not in servers:
        return JSONResponse({"error": f"'{name}' not found (remove env-based auto servers from .env)"}, status_code=404)
    del servers[name]
    _write_mcp_json(data)
    return JSONResponse({"ok": True})


@router.post("/api/mcp/test")
async def mcp_test(request: Request):
    """Test connectivity for an entry. body: {name, entry}. Does not write to mcp.json."""
    from pipeline.mcp_client import _normalize_entry, _make_transport
    from fastmcp import Client

    body = await request.json()
    name = (body.get("name") or "test").strip()
    entry = body.get("entry") or {}
    err = _validate_mcp_entry(name, entry)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    norm = _normalize_entry(name, entry)
    if not norm:
        return JSONResponse({"ok": False, "error": "normalization failed (command or url required)"}, status_code=400)
    try:
        transport = _make_transport(norm)
        async with Client(transport) as c:
            tools = await asyncio.wait_for(c.list_tools(), timeout=15)
        return JSONResponse({
            "ok": True,
            "tool_count": len(tools),
            "tools": [{"name": t.name, "description": (t.description or "")[:120]} for t in tools[:10]],
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=200)


@router.post("/api/mcp/parse-command")
async def mcp_parse_command(request: Request):
    """Parse a one-liner like `npx -y @modelcontextprotocol/server-filesystem ~/dev`
    into {name, entry} format. Does not write to mcp.json (for user confirmation)."""
    import shlex
    body = await request.json()
    text = (body.get("command") or "").strip()
    if not text:
        return JSONResponse({"error": "command 필수"}, status_code=400)

    if text.startswith("http://") or text.startswith("https://"):
        from urllib.parse import urlparse
        host = urlparse(text).hostname or "remote"
        name = host.split(".")[0].replace("-", "_")
        return JSONResponse({
            "name": name,
            "entry": {"url": text},
            "guessed_name": True,
        })

    try:
        tokens = shlex.split(text)
    except Exception as e:
        return JSONResponse({"error": f"parse error: {e}"}, status_code=400)
    if not tokens:
        return JSONResponse({"error": "empty command"}, status_code=400)

    cmd = tokens[0]
    args = tokens[1:]
    if cmd not in _MCP_ALLOWED_COMMANDS and not cmd.startswith("/"):
        return JSONResponse(
            {"error": f"disallowed command: '{cmd}'. Must be one of {sorted(_MCP_ALLOWED_COMMANDS)} or an absolute path"},
            status_code=400
        )

    name = None
    for tok in args:
        if tok.startswith("-") or tok.endswith(":"):
            continue
        if tok.startswith("@") and "/" in tok:
            pkg = tok.split("/", 1)[1]
            for prefix in ("server-", "mcp-server-"):
                if pkg.startswith(prefix):
                    name = pkg[len(prefix):]
                    break
            if not name:
                name = pkg
            break
        if tok.startswith("mcp-server-"):
            name = tok[len("mcp-server-"):]
            break
        if tok.startswith("server-"):
            name = tok[len("server-"):]
            break
        if cmd in {"python", "python3"} and tok == "-m":
            continue
        if not name:
            name = tok.replace("/", "_").replace(".", "_")
            break

    if not name:
        name = "mcp-server"
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-_")
    if not name:
        name = "mcp-server"

    entry = {"command": cmd, "args": args}
    return JSONResponse({"name": name, "entry": entry, "guessed_name": True})


@router.post("/api/mcp/reload")
async def mcp_reload():
    """Re-call init_mcp_tools and refresh TOOL_SCHEMAS — avoids a server restart."""
    from pipeline.mcp_client import init_mcp_tools
    from pipeline import tools as _tools

    _tools.TOOL_SCHEMAS[:] = [s for s in _tools.TOOL_SCHEMAS if "__" not in s.get("name", "")]
    try:
        mcp_schemas = await init_mcp_tools()
        added = 0
        for server_name, schemas in mcp_schemas.items():
            _tools.TOOL_SCHEMAS.extend(schemas)
            added += len(schemas)
        return JSONResponse({"ok": True, "servers": list(mcp_schemas.keys()), "tools": added})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
