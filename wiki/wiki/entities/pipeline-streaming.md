---
title: "pipeline/streaming.py — 에이전트 루프"
tags: [streaming, sse, core, tool-use]
sources: [concepts/tool-use-loop]
updated: 2026-06-02
status: active
---

# pipeline/streaming.py

핵심 에이전트 루프 구현. 진입점: `stream_gpt()`, `_build_request()`, `build_system()`.

## 주요 함수

| 함수 | 역할 |
|------|------|
| `stream_gpt()` | tool-use SSE 멀티라운드 루프 |
| `_build_request()` | 프로바이더별 요청 빌드 |
| `build_system()` | 페르소나+규칙+커맨드 시스템 프롬프트 조합 |
| `build_dynamic_preamble()` | 동적 컨텍스트 (캐싱 불가 부분) |

## 프롬프트 캐싱 주의

`build_system()`은 **정적 유지** 필수. 동적 컨텍스트는 `build_dynamic_preamble()`에 분리.
`build_system()`에 동적 값을 넣으면 캐시 키가 매 턴 달라져 캐싱 효과가 없어짐.

## Anthropic 특수 처리

Anthropic 프로바이더는 `_stream_sse()`에 별도 파싱 분기:
- `message_start` → usage 초기화
- `content_block_delta` → text_delta·input_json_delta
- `message_delta` → usage 갱신
- `message_stop` → 루프 종료

## 관련

- [[concepts/tool-use-loop]]
- [[entities/llm-gateway]]
