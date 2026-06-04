---
title: "pipeline/session_store.py — 세션 영속"
tags: [sqlite, session, messages, schema]
sources: [syntheses/known-pitfalls]
updated: 2026-06-02
status: active
---

# pipeline/session_store.py

대화 세션·메시지 영속. SQLite (`agent.db`).

## 스키마

```sql
conversations(uuid PK, source, name, created_at, updated_at, msg_count, working_dir, archived)
messages(uuid PK, source, conv_uuid, sender, text, char_len, created_at, updated_at, usage_meta)
```

## 함정 ⚠

**컬럼 이름이 직관과 다르다:**
- `conv_uuid` (not `session_uuid`)
- `sender` (not `role`) — 값: `"human"` | `"assistant"`
- `text` (not `content`)

과거에 `session_uuid/role/content`로 스키마를 만들고 CRUD는 `conv_uuid/sender/text`를 쓰는 불일치가 있었음. 현재는 수정됨. 새 DB 생성 시 반드시 `_ensure_schema()`를 통해 생성해야 충돌 없음.

## load_history 동작

`sender == "human"` → `user` 역할로 매핑. `sender == "assistant"` → `assistant`.

## 관련

- [[concepts/data-paths]]
- [[syntheses/known-pitfalls]]
