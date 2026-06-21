---
title: "MCP Server Registration/Invocation Pattern"
tags: [mcp, tools, integration]
sources: [entities/pipeline-streaming]
updated: 2026-06-02
status: active
---

# MCP Server Registration/Invocation Pattern

`init_mcp_tools()` / `call_mcp_tool()` in `pipeline/mcp_client.py`.

## Registration location

**The `mcp.json` in the user data dir** (= `data_paths.mcp_config_path()`).
The repo's `data/mcp.json` is **not read** → see [[concepts/data-paths]].

## Initialization timing

- `init_mcp_tools()` is called once in the `web/server.py` lifespan
- The channel bot uses `ensure_mcp_loaded()` (once per process)

## Tool envelope (kyte return format)

```json
{ "data": <list|dict|null>, "source": {"system": "...", "fetched_at": "..."}, "note": "<optional>" }
```

All kyte tools are read-only envelopes → safely allowed even in CE mode.

## Adding a new MCP server

1. Add an entry to `mcp.json` in the user data dir
2. Restart the server (the lifespan runs `init_mcp_tools()` again)
3. To allow it in CE mode, add the prefix to `tools._CE_ALLOWED_TOOLS` + modify both sides of `dispatch_tool` → [[concepts/ce-mode-gate]]

## Related

- [[concepts/data-paths]]
- [[concepts/ce-mode-gate]]
