---
title: "CE Mode Dual Gate"
tags: [ce-mode, security, tools, gate]
sources: [entities/pipeline-streaming, entities/llm-gateway]
updated: 2026-06-02
status: active
---

# CE Mode Dual Gate

A dual-defense pattern that controls exposure of local file/exec tools over remote channels (Telegram/Slack).

## Location of the two gates

| Gate | File | Function | Role |
|--------|------|------|------|
| Schema exposure | `pipeline/tools.py` | `get_schemas_for_mode()` | Hides the tool list itself from the LLM |
| Execution defense | `pipeline/tools.py` | `_CE_MODE_VAR` check inside `dispatch_tool()` | Blocks even bypass invocations |

## Pitfall ⚠

**You must modify both.** If you open only the schema, the model can't call the tool because it knows it exists but invocation is blocked. If you open only execution, the model has no schema and fails, saying "blocked because of CE mode."

## Current status

vega-agent currently has the **CE gate disabled** (personal use, so all tools are exposed).
The `ce_mode` argument and `_CE_ALLOWED_TOOLS` / `_CE_MODE_VAR` are preserved in the code for re-activation.

Exception: the `kyte__*` prefix is allowed even over remote channels (read-only envelope, the core purpose of the channel bot).

## plan_mode blocking

Separate from the CE gate. plan_mode blocking remains in place as-is.

## Checklist when re-activating

1. `get_schemas_for_mode()` — restore the CE-allowed tool whitelist
2. `dispatch_tool()` — restore the `_CE_MODE_VAR` check
3. A leaked channel bot token = exposure of the local machine, so this is mandatory before any remote exposure

## Related

- [[concepts/tool-use-loop]]
- [[entities/pipeline-streaming]]
