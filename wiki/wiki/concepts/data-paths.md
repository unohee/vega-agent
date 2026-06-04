---
title: "data_paths — user data dir 경로 해석"
tags: [data-paths, config, deployment]
sources: [entities/session-store]
updated: 2026-06-02
status: active
---

# data_paths — user data dir 경로 해석

모든 DB·config 경로의 단일 출처. `pipeline/data_paths.py`.

## 핵심 함수

| 함수 | 반환 | 기본값 |
|------|------|--------|
| `data_dir()` | user data dir 루트 | `~/Library/Application Support/VEGA/` (macOS) |
| `db_path()` | SQLite DB 경로 | `<data_dir>/agent.db` |
| `mcp_config_path()` | mcp.json 경로 | `<data_dir>/mcp.json` |

환경변수 `VEGA_DATA_DIR` 설정 시 기본값 오버라이드.

## 함정 ⚠

**`data/mcp.json` (레포 내)은 절대 읽지 않는다.** MCP 서버 등록은 반드시 user data dir에.

**`agent.db`는 `vega.db`가 아니다.** vega-core는 `agent.db`를 사용해 메인 VEGA의 `vega.db`와 스키마 충돌을 회피. `run_log.py`·`memory_inspector.py`의 하드코딩 폴백도 `agent.db`로 통일.

## 새 환경 초기화

빈 `VEGA_DATA_DIR`로 시작할 때 반드시:
```bash
python scripts/init_user_db.py
```
persona/events/entities/event_entities 테이블을 `vega_query._ensure_schema()`가 자동 생성.

## 관련

- [[concepts/mcp-integration]]
- [[entities/session-store]]
