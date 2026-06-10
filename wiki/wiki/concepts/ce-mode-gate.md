---
title: "CE 모드 이중 게이트"
tags: [ce-mode, security, tools, gate]
sources: [entities/pipeline-streaming, entities/llm-gateway]
updated: 2026-06-02
status: active
---

# CE 모드 이중 게이트

원격 채널(텔레그램·슬랙)에서 로컬 파일/exec 도구 노출을 제어하는 이중 방어 패턴.

## 두 게이트의 위치

| 게이트 | 파일 | 함수 | 역할 |
|--------|------|------|------|
| 스키마 노출 | `pipeline/tools.py` | `get_schemas_for_mode()` | LLM에게 도구 목록 자체를 숨김 |
| 실행 방어 | `pipeline/tools.py` | `dispatch_tool()` 내 `_CE_MODE_VAR` 체크 | 우회 호출 시에도 차단 |

## 함정 ⚠

**반드시 둘 다 수정해야 한다.** 스키마만 열면 모델이 도구를 알 수 없어 호출 안 함. 실행만 열면 스키마 없어 모델이 "CE 모드라 차단됨"이라며 실패.

## 현재 상태

현재 vega-agent는 **CE 게이트 비활성화** 상태 (개인용이라 전체 도구 노출).
`ce_mode` 인자와 `_CE_ALLOWED_TOOLS` / `_CE_MODE_VAR`는 재활성화용으로 코드에 보존.

예외: `kyte__*` prefix는 원격 채널에서도 허용 (read-only envelope, 채널 봇 핵심 목적).

## plan_mode 차단

CE 게이트와 별개. plan_mode 차단은 그대로 유지됨.

## 재활성화 시 체크리스트

1. `get_schemas_for_mode()` — CE 허용 도구 화이트리스트 복원
2. `dispatch_tool()` — `_CE_MODE_VAR` 체크 복원
3. 채널 봇 토큰 유출 = 로컬 머신 노출이므로 원격 노출 전 필수

## 관련

- [[concepts/tool-use-loop]]
- [[entities/pipeline-streaming]]
