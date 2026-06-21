---
title: "pipeline/session_store.py — session persistence"
tags: [sqlite, session, messages, schema]
sources: [syntheses/known-pitfalls]
updated: 2026-06-02
status: active
---

# pipeline/session_store.py

Persists conversation sessions and messages. SQLite (`agent.db`).

## Schema

```sql
conversations(uuid PK, source, name, created_at, updated_at, msg_count, working_dir, archived)
messages(uuid PK, source, conv_uuid, sender, text, char_len, created_at, updated_at, usage_meta)
```

## Pitfall ⚠

**The column names are counterintuitive:**
- `conv_uuid` (not `session_uuid`)
- `sender` (not `role`) — values: `"human"` | `"assistant"`
- `text` (not `content`)

There used to be a mismatch where the schema was created with `session_uuid/role/content` while the CRUD code used `conv_uuid/sender/text`. This is now fixed. When creating a new DB, you must create it through `_ensure_schema()` to avoid conflicts.

## load_history behavior

`sender == "human"` → mapped to the `user` role. `sender == "assistant"` → `assistant`.

## Related

- [[concepts/data-paths]]
- [[syntheses/known-pitfalls]]
