# VEGA Agent — Architecture

> This document is a structural map for other agents to quickly onboard onto this repo.
> For a human-reader overview, see the README.

## System Overview

VEGA Agent is a **local-first, model-agnostic LLM agent harness**. Its core abstraction
is "the LLM is the action layer; knowledge, rules, and memory persist in files/DBs outside the model."

From a product standpoint, VEGA is an app that fills **the gap between easy desktop AI apps
and developer-only terminal agent environments**. Apps like Claude Desktop/ChatGPT Desktop have
great accessibility, but are limited in permissions, customization, local execution, and workflow
persistence; combinations like Claude Code/Codex/OpenClaw/MCP/CLI are powerful, but hard to access
unless you are comfortable with the terminal, bash, config files, and daemon operations.

Therefore VEGA's design goal is to "deliver terminal-level AI capability with desktop-app UX."
It treats non-developer LLM power users, or people who want to become power users, as the primary
users, while leaving the internal structure and extension points open to developers.

Core product principles:
- **Local-first is the trust boundary**: core features must work locally without an account.
- **Bring your models**: models must be swappable, and the user's work state must remain in VEGA.
- **Desktop-simple, terminal-capable**: tool execution, file access, MCP, and approval flows should be powerful yet controllable from the UI.
- **No setup tax for power users**: provides comparable capability without having to install and operate the OpenClaw/MCP/CLI combination yourself.
- **Cloud is additive**: sync, backup, remote support, and management policy can be paid/cloud features, but must not replace the local core.

Core components:
- **Agent loop** (`pipeline/streaming.py`): SSE tool-use multi-round loop
- **Multi-provider** (`pipeline/llm_gateway.py`): ChatGPT/OpenRouter/LM Studio
- **Persistent memory** (`pipeline/session_store.py`, `vega_query.py`, `memory_store.py`)
- **Three-layer self-evolution** (`pipeline/compaction.py`): persona, rules, skills
- **Entry channels** (`web/server.py` FastAPI/SSE, `pipeline/channels/` Telegram/Slack bots)
- **External tool integration** (`pipeline/mcp_client.py`): MCP servers (e.g., kyte-portal work tools)

## Directory Tree (main)

```
pipeline/
  streaming.py      — GPT tool-use SSE loop (plan/research/CE modes)
  llm_gateway.py    — multi-provider router (OpenRouter=default, deepseek-v4-flash)
  tools.py          — tool registry + dispatch_tool + CE/plan mode gate
  tools_*.py        — tool implementations (google/code/web). office is an empty stub in vega-agent
  discord_bridge.py — Discord no-op stub (vega-agent uses Telegram/Slack)
  compaction.py     — 20-turn compaction + memory/rule update
  session_store.py  — session/message persistence (SQLite, conversations/messages)
  vega_query.py     — persona/event/entity queries + lexical FTS5 memory search
  mcp_client.py     — MCP server integration (stdio/sse), init_mcp_tools
  data_paths.py     — user data dir resolution (single source for all DB/config paths)
  self_improve.py   — tool failure → patch → verify
  channels/         — messenger channel adapters (new)
    core.py         — run_agent_turn shared core + session mapping
    telegram_bot.py — python-telegram-bot polling bot
    slack_bot.py    — slack_bolt Socket Mode bot
web/
  server.py         — FastAPI (REST + SSE), init_mcp_tools in lifespan
data/                — repo bundle defaults (commands/, agents/, llm_providers.json)
  agents/_default.md — distributor constitution (immutable)
  agents/RULES.md    — user rules (mutable)
sandbox/            — code execution Docker
desktop/            — Tauri v2 desktop app
tests/              — pytest (test_channel_kyte_e2e.py = channel↔kyte E2E)
```

## Module Responsibilities

| Module | Responsibility | Entry points |
|------|------|--------|
| streaming.py | tool-use loop, SSE streaming, CE/plan schema filter | `stream_gpt()`, `_build_request()`, `build_system()` |
| llm_gateway.py | provider routing, request building, tool group filtering | `build_request()`, `get_active_provider()` |
| tools.py | tool schemas + dispatch + CE/plan gate | `dispatch_tool()`, `get_schemas_for_mode()` |
| compaction.py | retrospection, memory persistence | `compact_history()` |
| session_store.py | conversation persistence (conversations/messages) | `append_message()`, `load_history()`, `create_session()` |
| vega_query.py | persona/event/entity + schema generation | `get_persona()`, `_ensure_schema()` |
| mcp_client.py | external MCP tool registration/invocation | `init_mcp_tools()`, `call_mcp_tool()`, `is_mcp_tool()` |
| channels/core.py | single-turn channel execution + session mapping | `run_agent_turn()`, `session_for()` |
| channels/telegram_bot.py | Telegram bot | `main()`, `build_application()` |
| channels/slack_bot.py | Slack bot (Socket Mode) | `main()`, `build_app()` |
| data_paths.py | user data dir path resolution | `data_dir()`, `mcp_config_path()`, `db_path()` |

## Data Flow

### Core agent loop
```
user message
  → build_system() (persona+rules+commands) [+ channels append kyte tool hints]
  → stream_gpt() loop (for _ in range(max_rounds)):
      _build_request() → get_schemas_for_mode(TOOL_SCHEMAS, ce_mode) → llm_gateway.build_request()
      → LLM SSE → token_q / tool_q (dual Queue) → on_token / tool accumulation
      → dispatch_tool() (run after passing the CE/plan gate) → re-inject function_call_output
  → final response
  → compact_history() every 20 turns (summary+memory+rules)
```

### Channel bot flow (Telegram/Slack)
```
messenger message (DM or @mention)
  → channels/{telegram,slack}_bot handler
  → channels/core.run_agent_turn(channel, conv_id, text, on_delta, ce_mode=True)
      → ensure_mcp_loaded() (once per process, merges MCP tools like kyte into TOOL_SCHEMAS)
      → session_for(channel, conv_id) → vega session ID (data/channel_sessions.json)
      → load_history() + current message → stream_gpt()
      → on_delta(accumulated text) on each on_token → channel progressively updates via edit_message/chat_update
      → append_message(sid, "human", ...) / append_message(sid, "assistant", ...)
  → final answer
```

### KYTE tool gateway (cross-repo)
```
kyte-portal: 10 integration_tools (Airtable/Gmail/Superthread/Calendar/Drive queries)
  → kyte_cli/mcp_server.py (stdio MCP server, INTEGRATION_TOOL_SPECS → MCP Tool)
  → registered as the "kyte" entry in [user data dir]/mcp.json
  → loaded at startup/first turn by vega-agent mcp_client.init_mcp_tools()
  → adds kyte__find_work, kyte__komca_lines ... to TOOL_SCHEMAS
  → dispatch goes through call_mcp_tool(), returns envelope {data, source, note}
```

## Key Types / Schemas

### SQLite (session_store.py — `db_path()` = `<data_dir>/vega.db`)
```
conversations(uuid PK, source, name, created_at, updated_at, msg_count, working_dir, archived)
messages(uuid PK, source, conv_uuid, sender, text, char_len, created_at, updated_at, usage_meta)
  -- sender: "human" | "assistant"; load_history maps only sender=="human" to user
```
### SQLite (vega_query.py — same vega.db, guaranteed by `_ensure_schema()`)
```
persona_sections(id PK, section_key, content, scope, version, is_active, notes, updated_at)
events(id PK, event_date, title, body, tags, created_at)
entities(id PK, name, kind, canonical_id, aliases_json, notes, first_seen, last_seen)
event_entities(event_id, entity_id, match_text)
```
### Channel session mapping (channels/core.py — `<repo>/data/channel_sessions.json`)
```
{ "telegram:<chat_id>": "<vega-session-uuid>", "slack:<channel>:<thread_ts>": "..." }
```
### Tool envelope (kyte tool returns — read-only)
```
{ "data": <list|dict|null>, "source": {"system": "...", "fetched_at": "..."}, "note": "<optional>" }
```

## Extension Points

- **New tool**: add a function in `tools_*.py` + register in `tools.py` TOOL_SCHEMAS/TOOL_FUNCTIONS
- **Add an STT provider**: register the endpoint in `_WELL_KNOWN_ENDPOINTS` of `pipeline/stt_gateway.py`, and set `provider`/`model`/`language` in the `stt` section of `data/llm_providers.json`
- **New UI language**: add a language code + translation pair to the `VEGA_STRINGS` object in `web/static/chat.html` and `dashboard.html`, with a language toggle button dropdown switch (planned for Phase 3)
- **New provider**: add it to `data/llm_providers.json` (or the user data dir copy)
- **New MCP server**: register it in **the user data dir's `mcp.json`** (not the repo `data/mcp.json` — see the landmine below)
- **New channel**: write an adapter in `channels/` → call `channels.core.run_agent_turn` and implement only progressive rendering with its own SDK
- **CE-mode-allowed tools**: add to `tools._CE_ALLOWED_TOOLS` or as a prefix exception (in both `get_schemas_for_mode` and `dispatch_tool`)

## Product / Business Boundary

Rather than directly replacing model companies' apps head-on, VEGA is better positioned as a
**personal agent workspace** that bridges multiple models and work tools. Claude, ChatGPT, Codex,
OpenRouter, and local models are all swappable action/reasoning engines; VEGA's assets are sessions,
memory, permissions, tool connections, execution records, and user workflows.

Recommended pricing boundary:
- **Free / Local**: local desktop app, BYOK/provider connections, local sessions/memory, basic tools, automatic updates.
- **Pro**: account login, sync across multiple Macs, encrypted backup, remote access, mobile/web clients, managed connectors.
- **Team / Enterprise**: org workspace, policy/permissions, audit logs, SSO, centralized connector management, remote support.

In implementation terms, cloud features should be an additive layer on top of the local-first core.
License checks must include an offline grace period, and local free features must not break due to
an account-verification failure.

## Don'ts / Landmines

- **mcp.json path**: `data_paths.mcp_config_path()` points to the **user data dir** (`~/Library/Application Support/VEGA/` or `$VEGA_DATA_DIR`). The repo's `data/mcp.json` is **not read**. MCP registration must go into the user data dir.
- **CE blocking is in two places**: schema exposure (`get_schemas_for_mode`) and execution defense (the `_CE_MODE_VAR` check in `dispatch_tool`). To allow a tool on a remote channel, you must fix **both**. If you unblock only one, the model fails saying "blocked because in CE mode."
- **session_store / vega_query schema**: `messages` uses `sender/text/conv_uuid` (not role/content). CRUD and `_ensure_schema` must match exactly — there's a history of a new DB breaking due to past mismatches.
- `data/agents/_default.md` is the distributor constitution — do not modify carelessly
- For prompt caching, keep `build_system()` static (dynamic context goes in `build_dynamic_preamble`)
- ChatGPT Codex rejects `max_output_tokens` (responses kind)
- Tools that depend on absent modules (linear_client, etc.) are excluded from schemas via import guards — otherwise self_improve runs amok

## Test Strategy

- Unit: `tests/test_*.py` — `pytest tests/`
- Integration E2E: `tests/test_channel_kyte_e2e.py` — channel core → kyte MCP → OpenRouter(deepseek-v4-flash) → Airtable round trip. Skipped if `OPENROUTER_API` is unset. For isolated runs, create an empty DB with `VEGA_DATA_DIR=/tmp/...`, then copy `mcp.json` and `llm_providers.json`.
