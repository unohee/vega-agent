---
title: "알려진 지뢰 목록"
tags: [pitfalls, bugs, traps, critical]
sources: [entities/session-store, concepts/ce-mode-gate, concepts/data-paths, concepts/mcp-integration]
updated: 2026-06-02
status: active
---

# 알려진 지뢰 목록

반복 실수 방지용. 코드 수정 전 반드시 확인.

---

## 1. mcp.json 경로 ⚠ 최다 실수

`data_paths.mcp_config_path()`는 **user data dir**를 가리킨다.
레포의 `data/mcp.json`은 **읽지 않는다**.

→ MCP 서버 등록은 반드시 `~/Library/Application Support/VEGA/mcp.json` (또는 `$VEGA_DATA_DIR/mcp.json`).

---

## 2. CE 모드 이중 게이트

원격 채널에 도구를 허용할 때 **스키마 노출 + 실행 방어 둘 다** 수정해야 한다.
하나만 풀면 모델이 "CE 모드라 차단됨"이라며 실패.

→ [[concepts/ce-mode-gate]] 참조.

---

## 3. session_store 컬럼 이름

`messages` 테이블 컬럼: `conv_uuid` / `sender` / `text`.
`session_uuid` / `role` / `content` 아님 — 과거 불일치로 깨졌던 이력 있음.

→ [[entities/session-store]] 참조.

---

## 4. vega.db vs agent.db

vega-core는 `agent.db` 사용. 메인 VEGA의 `vega.db`와 같은 user data dir를 공유해도 파일은 분리.
`run_log.py`, `memory_inspector.py` 하드코딩 폴백도 `agent.db`.

---

## 5. build_system() 정적 유지

프롬프트 캐싱을 위해 `build_system()` 반환값은 매 턴 동일해야 한다.
동적 컨텍스트(날짜, 현재 세션 등)는 `build_dynamic_preamble()`에 분리.
`build_system()`에 동적 값 추가 → 캐시 미스 폭발.

---

## 6. linear_* 도구 import 가드

`pipeline.linear_client` 없으면 `linear_*` 스키마가 TOOL_SCHEMAS에서 자동 제외.
없는 상태에서 강제 추가 → 도구 호출마다 실패 + `self_improve` 폭주.

---

## 7. Anthropic max_tokens 필수

Anthropic 프로바이더 요청에 `max_tokens` 없으면 API가 거부.
ChatGPT Codex(responses kind)는 반대로 `max_output_tokens` 거부.

---

## 8. 새 환경(빈 DB) 부팅 순서

새 `VEGA_DATA_DIR`에서 시작할 때:
1. `python scripts/init_user_db.py` 실행
2. `mcp.json` 복사 (필요 시)
3. `llm_providers.json` 복사 (필요 시)

`vega_query._ensure_schema()`가 persona/events/entities 테이블을 자동 생성하지만,
`scripts/init_user_db.py` 없이 서버 바로 실행 시 순서 문제 발생 가능.

---

## 9. create-dmg hang

`scripts/build_dmg.sh` 실행 시 `create-dmg`가 대화형 프롬프트에서 멈출 수 있음.
`--no-internet-enable` 플래그 추가로 해결.

→ [[topics/desktop-app]] 참조.
