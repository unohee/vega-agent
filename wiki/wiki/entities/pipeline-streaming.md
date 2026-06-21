---
title: "pipeline/streaming.py — agent loop"
tags: [streaming, sse, core, tool-use]
sources: [concepts/tool-use-loop]
updated: 2026-06-02
status: active
---

# pipeline/streaming.py

Core agent loop implementation. Entry points: `stream_gpt()`, `_build_request()`, `build_system()`.

## Key functions

| Function | Role |
|------|------|
| `stream_gpt()` | tool-use SSE multi-round loop |
| `_build_request()` | per-provider request building |
| `build_system()` | composes the persona + rules + commands system prompt |
| `build_dynamic_preamble()` | dynamic context (the non-cacheable part) |

## Prompt caching caveat

`build_system()` must remain **static**. Separate dynamic context into `build_dynamic_preamble()`.
Putting dynamic values into `build_system()` changes the cache key every turn, eliminating the caching benefit.

## Anthropic special handling

The Anthropic provider has a separate parsing branch in `_stream_sse()`:
- `message_start` → initialize usage
- `content_block_delta` → text_delta / input_json_delta
- `message_delta` → update usage
- `message_stop` → end the loop

## Related

- [[concepts/tool-use-loop]]
- [[entities/llm-gateway]]
