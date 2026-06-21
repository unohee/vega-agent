---
title: "Multi-Provider Design"
tags: [provider, openrouter, anthropic, openai, local]
sources: [entities/llm-gateway]
updated: 2026-06-02
status: active
---

# Multi-Provider Design

Composed of `data/llm_providers.json` + `pipeline/llm_gateway.py`.

## Current Defaults

- Active: `openrouter` (deepseek/deepseek-v4-flash)
- Two-tier setup: `tiers.local = lmstudio`, `tiers.cloud = openrouter`

## Adding a Provider

1. Add an entry to `data/llm_providers.json` (or to the copy in the user data dir)
2. `llm_gateway.build_request()` branches on `kind` — a new kind requires adding a branch
3. Anthropic requires schema conversion (`input_schema`, `max_tokens`)

## Install Wizard Integration

The wizard (`install_wizard.html` + `web/routers/onboarding.py`) connects the flow: provider selection → authentication → Keychain storage → `upsert_provider` → activation.

## Related

- [[entities/llm-gateway]]
- [[entities/pipeline-streaming]]
