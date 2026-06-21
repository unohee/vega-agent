---
title: "pipeline/llm_gateway.py — multi-provider router"
tags: [llm, provider, routing, anthropic, openai, openrouter]
sources: [topics/multi-provider]
updated: 2026-06-02
status: active
---

# pipeline/llm_gateway.py

Provider routing and request building. Entry points: `build_request()`, `get_active_provider()`.

## Supported provider kinds

| kind | Authentication | Notes |
|------|------|---------|
| `openrouter` | Bearer token | Default, deepseek-v4-flash |
| `openai` | Bearer token | `api.openai.com` directly |
| `anthropic` | `x-api-key` + `anthropic-version` | `/v1/messages` directly (not OpenAI-compatible) |
| `lmstudio` / `local` | None | OpenAI-compatible URL |

## Anthropic schema conversion

OpenAI tool `parameters` → Anthropic `input_schema`.
Wrap `system` in a cache_control block.
`max_tokens` is required (the API rejects the request without it).

## ChatGPT Codex caveat

Codex (responses kind) rejects the `max_output_tokens` parameter. It must be distinguished from other providers.

## Related

- [[topics/multi-provider]]
- [[entities/pipeline-streaming]]
