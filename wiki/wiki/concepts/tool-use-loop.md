---
title: "SSE Tool-Use Multi-Round Loop"
tags: [streaming, tool-use, sse, core]
sources: [entities/pipeline-streaming]
updated: 2026-06-02
status: active
---

# SSE Tool-Use Multi-Round Loop

The core agent loop implemented by `stream_gpt()` in `pipeline/streaming.py`.

## Structure

```
for _ in range(max_rounds):
    _build_request()
      → get_schemas_for_mode(TOOL_SCHEMAS, ce_mode)
      → llm_gateway.build_request()
    → LLM SSE stream
      → token_q (text tokens)
      → tool_q (accumulated tool calls)
    → dispatch_tool() → re-inject function_call_output
    → exit the loop if there are no tool calls
```

## Dual-queue pattern

- `token_q`: streaming text tokens → delivered immediately to the client via SSE
- `tool_q`: tool calls are accumulated until the stream ends, then executed in a batch
- Reason for separating the two queues: text streaming and tool execution can't run in parallel (the tool result must be included in the next request)

## Difference in the channel bot

The channel bot (`channels/core.py`) calls `stream_gpt(tier=)` → updates incrementally via the `on_delta` callback.
It uses `edit_message_text` / `chat_update` for the final rendering instead of SSE.

## Caveats

- When `max_rounds` is exceeded, the loop is force-terminated → returns the last partial response
- The Anthropic provider needs separate `message_start`/`content_block_delta`/`message_stop` SSE parsing (not OpenAI-compatible)

## Related

- [[entities/pipeline-streaming]]
- [[concepts/ce-mode-gate]]
- [[entities/llm-gateway]]
