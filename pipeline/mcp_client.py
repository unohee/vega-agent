# Created: 2026-05-18
# Purpose: VEGA MCP client layer — connects to external MCP servers and auto-registers tools
# Dependencies: fastmcp, mcp

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

logger = logging.getLogger(__name__)

# Detect prompt injection patterns in MCP tool descriptions
_INJECT_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?previous", re.IGNORECASE),
    re.compile(r"new\s+instruction", re.IGNORECASE),
    re.compile(r"\bsystem\s*:", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?previous", re.IGNORECASE),
    re.compile(r"override\s+(all\s+)?instructions", re.IGNORECASE),
]


def _sanitize_mcp_description(desc: str, qualified_name: str) -> str:
    """Detect injection patterns in an MCP tool description and neutralize. Truncates to 200 chars."""
    for pat in _INJECT_PATTERNS:
        if pat.search(desc):
            logger.warning(f"MCP [{qualified_name}] injection pattern detected in description — neutralized")
            return f"[sanitized tool: {qualified_name}]"
    return desc[:200]


# ── MCP server registry ────────────────────────────────────────────────────────
# transport: "stdio" | "sse"
# stdio: command + args
# sse:   url (+ optional headers)

def _load_env() -> None:
    """Load environment variables from the VEGA .env file (only for keys not already set).

    경로 해석은 keychain 과 공유한다 — 배포본(.app)에선 번들 임시경로가 아니라
    영속 사용자 데이터 루트(data_dir()/.env)를 본다.
    """
    try:
        from pipeline.keychain import _load_env_file
        env = _load_env_file()
    except Exception:
        return
    for k, v in env.items():
        if k not in os.environ:
            os.environ[k] = v

_load_env()


def _linear_entry() -> dict | None:
    key = os.getenv("LINEAR_API_KEY", "")
    if not key:
        return None
    return {
        "transport": "sse",
        "url": "https://mcp.linear.app/sse",
        "headers": {"Authorization": f"Bearer {key}"},
    }


from pipeline.data_paths import mcp_config_path as _mcp_config_path
_MCP_CONFIG_PATH = _mcp_config_path()


def _expand_env(value: Any) -> Any:
    """Expand ${VAR} / $VAR references in strings to environment variable values (used for headers/env)."""
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _normalize_entry(name: str, raw: dict) -> dict | None:
    """Normalize Claude Desktop format (mcpServers) to VEGA internal format.
    stdio: command + args (+ env)
    remote: url (+ headers), transport inferred from url (defaults to sse)."""
    raw = _expand_env(raw)
    if raw.get("command"):
        entry: dict = {
            "transport": "stdio",
            "command": raw["command"],
            "args": raw.get("args", []),
        }
        if raw.get("env"):
            entry["env"] = raw["env"]
        return entry
    url = raw.get("url")
    if url:
        return {
            "transport": raw.get("transport", "sse"),
            "url": url,
            "headers": raw.get("headers", {}),
        }
    logger.warning(f"MCP [{name}] config has no command or url — skipping")
    return None


def _load_registry() -> dict[str, dict]:
    """Merge data/mcp.json + Linear (from env) into MCP_REGISTRY. Reloads on every call (hot-reload)."""
    reg: dict[str, dict] = {}
    if _MCP_CONFIG_PATH.exists():
        try:
            data = json.loads(_MCP_CONFIG_PATH.read_text(encoding="utf-8"))
            for name, raw in (data.get("mcpServers") or {}).items():
                if not isinstance(raw, dict):
                    continue
                norm = _normalize_entry(name, raw)
                if norm:
                    reg[name] = norm
        except Exception as e:
            logger.warning(f"Failed to load data/mcp.json: {e}")
    # Auto-add Linear if LINEAR_API_KEY is set and not already in file
    if "linear" not in reg:
        lin = _linear_entry()
        if lin:
            reg["linear"] = lin
    return reg


MCP_REGISTRY: dict[str, dict] = _load_registry()

# ── Cache ─────────────────────────────────────────────────────────────────────
# { server_name: [ mcp.types.Tool, ... ] }
_tool_cache: dict[str, list] = {}

# Reverse mapping: tool qualified name → server name
_tool_server: dict[str, str] = {}


def _make_transport(cfg: dict):
    if cfg["transport"] == "stdio":
        kwargs = dict(
            command=cfg["command"],
            args=cfg.get("args", []),
            log_file=Path("/tmp/vega_mcp.log"),
        )
        if cfg.get("env"):
            # Merge current env with config env (preserves PATH etc.)
            kwargs["env"] = {**os.environ, **cfg["env"]}
        return StdioTransport(**kwargs)
    if cfg["transport"] in ("sse", "http"):
        from fastmcp.client.transports import SSETransport
        return SSETransport(url=cfg["url"], headers=cfg.get("headers", {}))
    raise ValueError(f"Unsupported transport: {cfg['transport']}")


async def _fetch_tools(server_name: str) -> list:
    cfg = MCP_REGISTRY[server_name]
    transport = _make_transport(cfg)
    async with Client(transport) as c:
        return await c.list_tools()


async def init_mcp_tools() -> dict[str, list[dict]]:
    """
    Collect tool listings from all MCP servers and convert to GPT tool-use schema format.
    Returns: { server_name: [schema, ...] }
    Failed server connections are silently skipped.
    """
    # Reload config + env to pick up latest mcp.json at server start
    global MCP_REGISTRY
    MCP_REGISTRY = _load_registry()

    result: dict[str, list[dict]] = {}

    for server_name, cfg in MCP_REGISTRY.items():
        try:
            tools = await _fetch_tools(server_name)
            _tool_cache[server_name] = tools
            schemas = []
            for t in tools:
                # Convert mcp Tool → GPT function schema
                qualified = f"{server_name}__{t.name}"
                schema: dict = {
                    "type": "function",
                    "name": qualified,
                    "description": _sanitize_mcp_description(t.description or "", qualified),
                    "parameters": t.inputSchema if t.inputSchema else {"type": "object", "properties": {}},
                }
                schemas.append(schema)
                _tool_server[qualified] = server_name
            result[server_name] = schemas
            logger.info(f"MCP [{server_name}] registered {len(tools)} tools")
        except Exception as e:
            logger.warning(f"MCP [{server_name}] initialization failed: {e}")

    return result


async def call_mcp_tool(qualified_name: str, arguments: dict) -> str:
    """
    qualified_name: "{server_name}__{tool_name}" format
    Returns: JSON string (same contract as dispatch_tool)
    """
    server_name = _tool_server.get(qualified_name)
    if not server_name:
        return json.dumps({"error": f"MCP tool not found: {qualified_name}"})
    if server_name not in MCP_REGISTRY:
        # stale _tool_server 엔트리 — 서버가 제거됐거나 재로드 전
        _tool_server.pop(qualified_name, None)
        return json.dumps({"error": f"MCP server '{server_name}' not in registry (stale)"})

    tool_name = qualified_name[len(server_name) + 2:]  # strip "__" prefix
    cfg = MCP_REGISTRY[server_name]

    try:
        transport = _make_transport(cfg)
        async with Client(transport) as c:
            result = await c.call_tool(tool_name, arguments)
        # Concatenate text blocks from CallToolResult.content
        texts = [
            block.text
            for block in result.content
            if hasattr(block, "text")
        ]
        raw = json.dumps({"result": "\n\n".join(texts)}, ensure_ascii=False)
        from pipeline.injection_guard import guard_tool_result
        return guard_tool_result(qualified_name, raw)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def is_mcp_tool(name: str) -> bool:
    return name in _tool_server
