---
title: "VEGA Agent Wiki — Index"
tags: [index]
updated: 2026-06-02
status: active
---

# VEGA Agent Wiki

Development knowledge base for the VEGA Agent harness.

## Core concepts

- [[concepts/tool-use-loop]] — SSE multi-round tool-use loop structure
- [[concepts/ce-mode-gate]] — CE mode dual gate (schema exposure + execution defense)
- [[concepts/compaction]] — 20-turn self-evolution (persona/rules/memory updates)
- [[concepts/data-paths]] — user data dir based path resolution pattern
- [[concepts/mcp-integration]] — MCP server registration/invocation pattern

## Module entities

- [[entities/pipeline-streaming]] — `pipeline/streaming.py` stream_gpt() loop
- [[entities/llm-gateway]] — `pipeline/llm_gateway.py` multi-provider router
- [[entities/session-store]] — `pipeline/session_store.py` SQLite persistence
- [[entities/stt-gateway]] — `pipeline/stt_gateway.py` STT provider gateway

## Topic syntheses

- [[topics/multi-provider]] — Multi-provider design (OpenAI/OpenRouter/Anthropic/Local)
- [[topics/stt-integration]] — STT/Whisper integration + graceful failure pattern
- [[topics/i18n]] — UI internationalization (KO/EN, expansion roadmap)
- [[topics/desktop-app]] — Tauri v2 desktop app + DMG distribution pipeline

## Known landmines

- [[syntheses/known-pitfalls]] — mcp.json path, CE dual gate, session_store schema mismatch, etc.

## Change log

- [[log]] — Change history of the wiki itself
