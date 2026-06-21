---
title: "data_paths — user data dir path resolution"
tags: [data-paths, config, deployment]
sources: [entities/session-store]
updated: 2026-06-02
status: active
---

# data_paths — user data dir path resolution

The single source of truth for all DB/config paths. `pipeline/data_paths.py`.

## Core functions

| Function | Returns | Default |
|------|------|--------|
| `data_dir()` | user data dir root | `~/Library/Application Support/VEGA/` (macOS) |
| `db_path()` | SQLite DB path | `<data_dir>/agent.db` |
| `mcp_config_path()` | mcp.json path | `<data_dir>/mcp.json` |

Setting the `VEGA_DATA_DIR` environment variable overrides the default.

## Pitfall ⚠

**`data/mcp.json` (inside the repo) is never read.** MCP server registration must be in the user data dir.

**`agent.db` is not `vega.db`.** vega-agent uses `agent.db` to avoid a schema collision with the main VEGA's `vega.db`. The hardcoded fallbacks in `run_log.py` and `memory_inspector.py` are also unified to `agent.db`.

## Initializing a new environment

When starting with an empty `VEGA_DATA_DIR`, you must run:
```bash
python scripts/init_user_db.py
```
`vega_query._ensure_schema()` automatically creates the persona/events/entities/event_entities tables.

## Related

- [[concepts/mcp-integration]]
- [[entities/session-store]]
