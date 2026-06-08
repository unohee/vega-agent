---
title: "멀티 프로바이더 설계"
tags: [provider, openrouter, anthropic, openai, local]
sources: [entities/llm-gateway]
updated: 2026-06-02
status: active
---

# 멀티 프로바이더 설계

`data/llm_providers.json` + `pipeline/llm_gateway.py`로 구성.

## 현재 기본값

- Active: `openrouter` (deepseek/deepseek-v4-flash)
- 2단 tier: `tiers.local = lmstudio`, `tiers.cloud = openrouter`

## 프로바이더 추가

1. `data/llm_providers.json`에 항목 추가 (또는 user data dir 사본에)
2. `llm_gateway.build_request()`가 `kind`로 분기 — 새 kind는 분기 추가 필요
3. Anthropic은 스키마 변환 필수 (`input_schema`, `max_tokens`)

## 설치 마법사 통합

마법사(`install_wizard.html` + `web/routers/onboarding.py`)가 프로바이더 선택 → 인증 → Keychain 저장 → `upsert_provider` → 활성화 흐름으로 연결.

## 관련

- [[entities/llm-gateway]]
- [[entities/pipeline-streaming]]
