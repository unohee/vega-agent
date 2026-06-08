# Created: 2026-05-18
# Purpose: VEGA session persistence — save/restore conversations in conversations/messages tables
# Dependencies: sqlite3 (stdlib)
# Test Status: untested

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline.data_paths import db_path as _db_path

# Compatibility alias — other modules that import session_store.DB_PATH still work
DB_PATH = _db_path()
SOURCE = "vega"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema() -> None:
    """Create tables + migrate missing columns (idempotent). Safe for new user DBs."""
    with _conn() as conn:
        # NOTE: 컬럼명은 CRUD 함수(create_session/append_message/load_history 등)가
        # 실제로 INSERT/SELECT 하는 것과 일치해야 한다. conversations 는 uuid 를 PK 로,
        # messages 는 conv_uuid/sender/text/char_len/updated_at 를 쓴다.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                uuid        TEXT PRIMARY KEY,
                source      TEXT NOT NULL DEFAULT 'vega',
                name        TEXT NOT NULL DEFAULT 'VEGA 세션',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                msg_count   INTEGER NOT NULL DEFAULT 0,
                working_dir TEXT,
                archived    INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                uuid        TEXT PRIMARY KEY,
                source      TEXT NOT NULL DEFAULT 'vega',
                conv_uuid   TEXT NOT NULL,
                sender      TEXT NOT NULL,
                text        TEXT NOT NULL,
                char_len    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                usage_meta  TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conv "
            "ON messages(source, conv_uuid, created_at)"
        )
        # Migrate existing DB — add columns if missing
        cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
        if "working_dir" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN working_dir TEXT")
        if "archived" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "usage_meta" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN usage_meta TEXT")
        # events: assistant 메시지의 인터리빙 구조(텍스트 세그먼트 + 도구 호출)를
        # JSON으로 저장 → 재방문 시 라이브와 동일한 시간순 복원. 구 메시지는 NULL(텍스트 폴백).
        if "events" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN events TEXT")


_ensure_schema()
# Restrict DB file to owner only — prevent access by other users on the same machine
try:
    Path(DB_PATH).chmod(0o600)
except Exception:
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Session (conversation) management ────────────────────────────────────────

def create_session(title: str = "VEGA 세션") -> str:
    """Create a new session and return the session_uuid."""
    sid = str(uuid.uuid4())
    now = _now()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO conversations (uuid, source, name, created_at, updated_at, msg_count)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (sid, SOURCE, title, now, now),
        )
    return sid


def list_sessions(limit: int = 20, include_archived: bool = False) -> list[dict]:
    """Return recent session list.
    include_archived=False (default): only archived=0. True returns all (for UI toggle).
    """
    where = "source = ?" + ("" if include_archived else " AND COALESCE(archived,0) = 0")
    with _conn() as conn:
        rows = conn.execute(
            f"""SELECT uuid, name, created_at, updated_at, msg_count, working_dir,
                       COALESCE(archived, 0) AS archived
               FROM conversations
               WHERE {where}
               ORDER BY updated_at DESC
               LIMIT ?""",
            (SOURCE, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def set_archived(session_uuid: str, archived: bool) -> bool:
    """Toggle the session archived flag. Returns False if the row does not exist."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE conversations SET archived=?, updated_at=? WHERE source=? AND uuid=?",
            (1 if archived else 0, _now(), SOURCE, session_uuid),
        )
        return cur.rowcount > 0


def get_session(session_uuid: str) -> dict | None:
    """Retrieve session metadata."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE source=? AND uuid=?",
            (SOURCE, session_uuid),
        ).fetchone()
    return dict(row) if row else None


def rename_session(session_uuid: str, title: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE conversations SET name=?, updated_at=? WHERE source=? AND uuid=?",
            (title, _now(), SOURCE, session_uuid),
        )


def set_working_dir(session_uuid: str, working_dir: str | None) -> None:
    """Set the session working directory. None clears it (falls back to home)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE conversations SET working_dir=?, updated_at=? WHERE source=? AND uuid=?",
            (working_dir, _now(), SOURCE, session_uuid),
        )


def get_working_dir(session_uuid: str) -> str | None:
    """Retrieve the session working directory. Returns None if not set."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT working_dir FROM conversations WHERE source=? AND uuid=?",
            (SOURCE, session_uuid),
        ).fetchone()
    return row["working_dir"] if row and row["working_dir"] else None


# ── Message storage / retrieval ───────────────────────────────────────────────

def append_message(
    session_uuid: str, role: str, text: str,
    usage_meta: dict | None = None,
    events: list | None = None,
) -> str:
    """
    Save a message and return the message_uuid.
    role: 'human' | 'assistant'
    usage_meta: assistant 메시지의 LLM usage stats (model/tokens/cost/tok_per_sec/ttft_sec).
                None이면 컬럼은 NULL로 저장.
    events: assistant 메시지의 인터리빙 구조 — [{"type":"text","data":...},
            {"type":"tool", "name", "label", "summary", "args", "status", ...}] 순서 배열.
            재방문 시 라이브와 동일한 시간순 복원에 사용. None이면 텍스트 폴백.
    """
    import json as _json
    mid = str(uuid.uuid4())
    now = _now()
    usage_str = _json.dumps(usage_meta, ensure_ascii=False) if usage_meta else None
    events_str = _json.dumps(events, ensure_ascii=False) if events else None
    with _conn() as conn:
        conn.execute(
            """INSERT INTO messages (uuid, source, conv_uuid, sender, text, char_len, created_at, updated_at, usage_meta, events)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mid, SOURCE, session_uuid, role, text, len(text), now, now, usage_str, events_str),
        )
        conn.execute(
            """UPDATE conversations SET msg_count=msg_count+1, updated_at=?
               WHERE source=? AND uuid=?""",
            (now, SOURCE, session_uuid),
        )
    return mid


def load_history(session_uuid: str) -> list[dict]:
    """
    Return the full conversation history for a session (for LLM input — content only).
    Returns: [{"role": "user"|"assistant", "content": str}]
    For UI metadata (usage), use load_history_with_meta instead.
    """
    with _conn() as conn:
        rows = conn.execute(
            """SELECT sender, text FROM messages
               WHERE source=? AND conv_uuid=?
               ORDER BY created_at ASC""",
            (SOURCE, session_uuid),
        ).fetchall()
    return [
        {"role": "user" if r["sender"] == "human" else "assistant", "content": r["text"]}
        for r in rows
    ]


def load_history_with_meta(session_uuid: str) -> list[dict]:
    """UI용 히스토리. usage_meta·events JSON 파싱해서 함께 반환.
    events가 있으면 재방문 시 인터리빙(텍스트↔도구 시간순) 복원, 없으면 텍스트 폴백."""
    import json as _json
    with _conn() as conn:
        rows = conn.execute(
            """SELECT sender, text, created_at, usage_meta, events FROM messages
               WHERE source=? AND conv_uuid=?
               ORDER BY created_at ASC""",
            (SOURCE, session_uuid),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        keys = r.keys()
        meta: dict | None = None
        meta_raw = r["usage_meta"] if "usage_meta" in keys else None
        if meta_raw:
            try:
                meta = _json.loads(meta_raw)
            except Exception:
                meta = None
        events: list | None = None
        events_raw = r["events"] if "events" in keys else None
        if events_raw:
            try:
                events = _json.loads(events_raw)
            except Exception:
                events = None
        out.append({
            "role": "human" if r["sender"] == "human" else "assistant",
            "content": r["text"],
            "ts": r["created_at"],
            "usage": meta,
            "events": events,
        })
    return out


def get_or_create_session(session_uuid: str | None) -> str:
    """Return the session_uuid if valid, otherwise create a new session."""
    if session_uuid:
        existing = get_session(session_uuid)
        if existing:
            return session_uuid
    return create_session()


def delete_session(session_uuid: str) -> None:
    with _conn() as conn:
        conn.execute(
            "DELETE FROM messages WHERE source=? AND conv_uuid=?",
            (SOURCE, session_uuid),
        )
        conn.execute(
            "DELETE FROM conversations WHERE source=? AND uuid=?",
            (SOURCE, session_uuid),
        )


def clean_sessions(
    keep_min_messages: int = 2,
    max_age_days: int | None = 90,
    dry_run: bool = False,
    is_trivial_fn: "Callable[[str, str, int], bool] | None" = None,
) -> dict:
    """
    Remove unnecessary sessions.
    - msg_count < keep_min_messages: empty or single-message sessions
    - older than max_age_days & msg_count < 5: old and short sessions
    - is_trivial_fn(uuid, name, msg_count) → True: callback to mark session as trivial

    Returns: {"deleted": N, "kept": N, "trivial": N, "dry_run": bool}
    """
    from datetime import timedelta
    cutoff = None
    if max_age_days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()

    with _conn() as conn:
        rows = conn.execute(
            "SELECT uuid, name, msg_count, updated_at FROM conversations WHERE source=?",
            (SOURCE,),
        ).fetchall()

        to_delete = []
        trivial_count = 0
        for r in rows:
            if r["msg_count"] < keep_min_messages:
                to_delete.append(r["uuid"])
            elif cutoff and r["updated_at"] and r["updated_at"] < cutoff and r["msg_count"] < 5:
                to_delete.append(r["uuid"])
            elif is_trivial_fn and is_trivial_fn(r["uuid"], r["name"] or "", r["msg_count"]):
                to_delete.append(r["uuid"])
                trivial_count += 1

        if not dry_run:
            for uid in to_delete:
                conn.execute("DELETE FROM messages WHERE source=? AND conv_uuid=?", (SOURCE, uid))
                conn.execute("DELETE FROM conversations WHERE source=? AND uuid=?", (SOURCE, uid))

    return {
        "deleted": len(to_delete),
        "kept": len(rows) - len(to_delete),
        "trivial": trivial_count,
        "dry_run": dry_run,
    }
