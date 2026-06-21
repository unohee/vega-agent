---
title: "List of known landmines"
tags: [pitfalls, bugs, traps, critical]
sources: [entities/session-store, concepts/ce-mode-gate, concepts/data-paths, concepts/mcp-integration]
updated: 2026-06-02
status: active
---

# List of known landmines

For preventing recurring mistakes. Always check before modifying code.

---

## 1. mcp.json path ⚠ most common mistake

`data_paths.mcp_config_path()` points to the **user data dir**.
It does **not read** the repo's `data/mcp.json`.

→ MCP server registration must go in `~/Library/Application Support/VEGA/mcp.json` (or `$VEGA_DATA_DIR/mcp.json`).

---

## 2. CE mode double gate

When allowing a tool on a remote channel, you must fix **both schema exposure and execution defense**.
If you open only one, the model fails saying "blocked because of CE mode."

→ See [[concepts/ce-mode-gate]].

---

## 3. session_store column names

`messages` table columns: `conv_uuid` / `sender` / `text`.
Not `session_uuid` / `role` / `content` — there is a history of breakage from a past mismatch.

→ See [[entities/session-store]].

---

## 4. vega.db vs agent.db

vega-agent uses `agent.db`. Even if it shares the same user data dir as the main VEGA's `vega.db`, the files are separate.
The hardcoded fallbacks in `run_log.py` and `memory_inspector.py` are also `agent.db`.

---

## 5. build_system() must stay static

For prompt caching, the return value of `build_system()` must be identical every turn.
Separate dynamic context (date, current session, etc.) into `build_dynamic_preamble()`.
Adding dynamic values to `build_system()` → cache-miss explosion.

---

## 6. linear_* tool import guard

If `pipeline.linear_client` is missing, the `linear_*` schemas are automatically excluded from TOOL_SCHEMAS.
Force-adding them while it is missing → failures on every tool call + a `self_improve` storm.

---

## 7. Anthropic max_tokens required

If an Anthropic provider request has no `max_tokens`, the API rejects it.
Conversely, ChatGPT Codex (responses kind) rejects `max_output_tokens`.

---

## 8. Boot order for a new environment (empty DB)

When starting in a new `VEGA_DATA_DIR`:
1. Run `python scripts/init_user_db.py`
2. Copy `mcp.json` (if needed)
3. Copy `llm_providers.json` (if needed)

`vega_query._ensure_schema()` auto-creates the persona/events/entities tables, but
running the server directly without `scripts/init_user_db.py` can cause ordering problems.

---

## 9. create-dmg hang

When running `scripts/build_dmg.sh`, `create-dmg` may hang on an interactive prompt.
Resolved by adding the `--no-internet-enable` flag.

→ See [[topics/desktop-app]].
