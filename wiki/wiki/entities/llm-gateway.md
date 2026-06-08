---
title: "pipeline/llm_gateway.py — 멀티 프로바이더 라우터"
tags: [llm, provider, routing, anthropic, openai, openrouter]
sources: [topics/multi-provider]
updated: 2026-06-02
status: active
---

# pipeline/llm_gateway.py

프로바이더 라우팅·요청 빌드. 진입점: `build_request()`, `get_active_provider()`.

## 지원 프로바이더 종류

| kind | 인증 | 특이사항 |
|------|------|---------|
| `openrouter` | Bearer token | 기본값, deepseek-v4-flash |
| `openai` | Bearer token | `api.openai.com` 직접 |
| `anthropic` | `x-api-key` + `anthropic-version` | `/v1/messages` 직접 (OpenAI 호환 아님) |
| `lmstudio` / `local` | 없음 | OpenAI 호환 URL |

## Anthropic 스키마 변환

OpenAI tool `parameters` → Anthropic `input_schema`.
`system`을 cache_control 블록으로 래핑.
`max_tokens` 필수 (없으면 API 거부).

## ChatGPT Codex 주의

Codex (responses kind)는 `max_output_tokens` 파라미터를 거부. 다른 프로바이더와 구분 필요.

## 관련

- [[topics/multi-provider]]
- [[entities/pipeline-streaming]]
