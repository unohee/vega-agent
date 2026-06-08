---
title: "SSE Tool-Use 멀티라운드 루프"
tags: [streaming, tool-use, sse, core]
sources: [entities/pipeline-streaming]
updated: 2026-06-02
status: active
---

# SSE Tool-Use 멀티라운드 루프

`pipeline/streaming.py`의 `stream_gpt()`가 구현하는 핵심 에이전트 루프.

## 구조

```
for _ in range(max_rounds):
    _build_request()
      → get_schemas_for_mode(TOOL_SCHEMAS, ce_mode)
      → llm_gateway.build_request()
    → LLM SSE 스트림
      → token_q (텍스트 토큰)
      → tool_q (도구 호출 누적)
    → dispatch_tool() → function_call_output 재주입
    → 도구 호출 없으면 루프 종료
```

## 이중 큐 패턴

- `token_q`: 스트리밍 텍스트 토큰 → 클라이언트 SSE로 즉시 전달
- `tool_q`: 도구 호출은 스트림이 끝날 때까지 누적 후 일괄 실행
- 두 큐를 분리하는 이유: 텍스트 스트리밍과 도구 실행이 병렬 진행 불가 (도구 결과를 다음 요청에 포함해야 함)

## 채널 봇에서의 차이

채널 봇(`channels/core.py`)은 `stream_gpt(tier=)`로 호출 → `on_delta` 콜백으로 점진 갱신.
SSE 대신 `edit_message_text` / `chat_update`로 최종 렌더링.

## 주의

- `max_rounds` 초과 시 루프 강제 종료 → 마지막 partial 응답 반환
- Anthropic 프로바이더는 `message_start`/`content_block_delta`/`message_stop` SSE 파싱이 별도로 필요 (OpenAI 호환 아님)

## 관련

- [[entities/pipeline-streaming]]
- [[concepts/ce-mode-gate]]
- [[entities/llm-gateway]]
