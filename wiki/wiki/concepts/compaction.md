---
title: "20턴 자기진화 컴팩션"
tags: [compaction, self-improve, memory, persona]
sources: [entities/pipeline-streaming]
updated: 2026-06-02
status: active
---

# 20턴 자기진화 컴팩션

`pipeline/compaction.py`의 `compact_history()`.

## 트리거

`stream_gpt()` 루프에서 20턴마다 자동 호출.

## 3겹 자기진화

1. **대화 요약** — 긴 히스토리를 압축해 컨텍스트 창 절약
2. **메모리 갱신** — 새 사실·엔티티를 LanceDB 벡터 메모리(`memory_store.py`)에 추가
3. **규칙 갱신** — 반복 패턴·선호를 `data/agents/RULES.md`에 자동 반영

## 불변 영역

`data/agents/_default.md`는 배포자 헌법 — 컴팩션이 절대 수정 안 함. 사람도 함부로 수정 금지.

## 관련

- [[entities/pipeline-streaming]]
- [[concepts/tool-use-loop]]
