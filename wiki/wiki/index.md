---
title: "VEGA Agent Wiki — 인덱스"
tags: [index]
updated: 2026-06-02
status: active
---

# VEGA Agent Wiki

VEGA Agent 에이전트 하네스 개발 지식 베이스.

## 핵심 개념

- [[concepts/tool-use-loop]] — SSE 멀티라운드 tool-use 루프 구조
- [[concepts/ce-mode-gate]] — CE 모드 이중 게이트 (스키마 노출 + 실행 방어)
- [[concepts/compaction]] — 20턴 자기진화 (페르소나·규칙·메모리 갱신)
- [[concepts/data-paths]] — user data dir 기반 경로 해석 패턴
- [[concepts/mcp-integration]] — MCP 서버 등록/호출 패턴

## 모듈 엔티티

- [[entities/pipeline-streaming]] — `pipeline/streaming.py` stream_gpt() 루프
- [[entities/llm-gateway]] — `pipeline/llm_gateway.py` 멀티 프로바이더 라우터
- [[entities/session-store]] — `pipeline/session_store.py` SQLite 영속
- [[entities/stt-gateway]] — `pipeline/stt_gateway.py` STT 프로바이더 게이트웨이

## 주제별 종합

- [[topics/multi-provider]] — 멀티 프로바이더 설계 (OpenAI/OpenRouter/Anthropic/Local)
- [[topics/stt-integration]] — STT/Whisper 통합 + graceful failure 패턴
- [[topics/i18n]] — UI 다국어 지원 (KO/EN, 확장 로드맵)
- [[topics/desktop-app]] — Tauri v2 데스크톱 앱 + DMG 배포 파이프라인

## 알려진 지뢰

- [[syntheses/known-pitfalls]] — mcp.json 경로, CE 이중 게이트, session_store 스키마 불일치 등

## 변경 로그

- [[log]] — 위키 자체 변경 이력
