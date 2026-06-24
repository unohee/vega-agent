---
title: "20-Turn Self-Evolution Compaction"
tags: [compaction, self-improve, memory, persona]
sources: [entities/pipeline-streaming]
updated: 2026-06-02
status: active
---

# 20-Turn Self-Evolution Compaction

`compact_history()` in `pipeline/compaction.py`.

## Trigger

Automatically invoked every 20 turns in the `stream_gpt()` loop.

## Three-layer self-evolution

1. **Conversation summary** — compress long history to save the context window
2. **Memory update** — extract facts/entities into lexical memory tables (FTS5 via `vega_query.py`)
3. **Rule update** — automatically reflect recurring patterns/preferences into `data/agents/RULES.md`

## Immutable region

`data/agents/_default.md` is the deployer's constitution — compaction never modifies it. Humans should not modify it carelessly either.

## Related

- [[entities/pipeline-streaming]]
- [[concepts/tool-use-loop]]
