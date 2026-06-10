# VEGA Agent Wiki — Schema

VEGA Agent 에이전트 하네스의 개발 지식 베이스. 아키텍처 결정, 모듈 동작, 버그 기록, 실험 결과, 운용 노하우를 LLM이 소유·관리하는 구조화 위키.

## 디렉터리

```
wiki/
├── raw/          # 외부 소스 사본 (git log, Linear export 등)
├── wiki/         # LLM 소유 — 사람은 읽기 권장
│   ├── index.md
│   ├── log.md
│   ├── entities/   # 모듈·시스템·사람 (pipeline/, server.py, LM Studio 등)
│   ├── concepts/   # 핵심 개념·패턴 (tool-use loop, CE mode, compaction 등)
│   ├── sources/    # 1:1 소스 페이지 (commit, 버그 리포트)
│   ├── topics/     # 주제별 종합 (STT 통합, i18n, 멀티 프로바이더 등)
│   └── syntheses/  # 비교·분석·추천 (프로바이더 선택, 성능 트레이드오프 등)
└── SCHEMA.md
```

## 네이밍

- 모든 페이지: kebab-case (`stt-gateway-design.md`)
- Commit 기반: `commit-{sha7}.md`
- 버그/이슈: `bug-{키워드}.md`
- 기능: 토픽 이름 그대로 (`multi-provider-routing.md`)

## 프론트매터

```yaml
---
title: "사람이 읽는 제목"
tags: [stt, provider, pipeline]
sources: [commit-0b73449, bug-session-store]
updated: 2026-06-02
status: active | archived | superseded
---
```

## 교차 참조

- 같은 위키 안: `[[concepts/tool-use-loop]]` 또는 `[[entities/pipeline-streaming]]`
- Commit: `[0b73449](../..)`
- 외부 문서: `[ARCHITECTURE.md](../../ARCHITECTURE.md)`

## 워크플로우

- **소스의 1차 진실은 코드 + git commit message + ARCHITECTURE.md**. 위키는 재정리·교차 참조·모순 탐지 위주.
- **새 기능·버그 수정 후** 관련 sources/topics 페이지 갱신.
- **함정(지뢰) 발견 시** concepts/ 또는 topics/에 즉시 기록 — 반복 실수 방지.

## 모순 표시

```markdown
> ⚠ [[sources/commit-abc1234]]는 X가 원인이라 결론. [[sources/commit-def5678]]는 Y가 실제 원인이라 수정.
```

## 사람 vs LLM 책임

- **LLM**: 모든 페이지 작성·갱신·교차 참조·index/log 관리.
- **사람**: SCHEMA 변경 결정, 큰 카테고리 추가/삭제, 작업 우선순위 지시.
