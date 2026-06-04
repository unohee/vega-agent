# Created: 2026-05-21
# Purpose: pipeline/session_store.py 단위 테스트 — 인메모리 SQLite 사용
# Dependencies: pipeline/session_store.py
# Test Status: 신규

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def patch_db(tmp_path, monkeypatch):
    """session_store DB_PATH를 임시 파일로 교체 + 스키마 초기화."""
    db_path = tmp_path / "test_vega.db"
    monkeypatch.setattr("pipeline.session_store.DB_PATH", db_path)

    # conversations + messages 테이블 생성 (실제 session_store.py 스키마에 맞춤)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid       TEXT UNIQUE NOT NULL,
            source     TEXT NOT NULL DEFAULT 'vega',
            name       TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            msg_count  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid       TEXT UNIQUE NOT NULL,
            source     TEXT NOT NULL DEFAULT 'vega',
            conv_uuid  TEXT NOT NULL,
            sender     TEXT NOT NULL,
            text       TEXT NOT NULL,
            char_len   INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    # 누락 컬럼 마이그레이션 (working_dir 등) — 실제 스키마와 동기화
    import pipeline.session_store as _ss
    _ss._ensure_schema()


from pipeline.session_store import (
    append_message,
    create_session,
    delete_session,
    get_or_create_session,
    get_session,
    list_sessions,
    load_history,
    load_history_with_meta,
    rename_session,
)


class TestEventsPersistence:
    """인터리빙 events 영속화 — 재방문 시 텍스트↔도구 시간순 복원의 전제."""

    def test_events_roundtrip(self):
        sid = create_session("evt")
        events = [
            {"type": "text", "data": "실행할게."},
            {"type": "tool", "name": "bash_exec", "label": "실행 중",
             "call_id": "c1", "status": "done", "summary": "✓ echo hello"},
            {"type": "text", "data": "hello"},
        ]
        append_message(sid, "assistant", "실행할게.\nhello", events=events)
        hist = load_history_with_meta(sid)
        assert len(hist) == 1
        assert hist[0]["events"] == events  # 순서·내용 그대로 복원

    def test_events_none_when_omitted(self):
        """events 없이 저장한 (순수 텍스트) 메시지는 events=None."""
        sid = create_session("evt2")
        append_message(sid, "assistant", "그냥 텍스트 답변")
        hist = load_history_with_meta(sid)
        assert hist[0]["events"] is None

    def test_events_and_usage_independent(self):
        """events와 usage_meta가 서로 간섭 없이 함께 저장된다."""
        sid = create_session("evt3")
        usage = {"model": "x", "output_tokens": 10}
        events = [{"type": "tool", "name": "t", "status": "done", "summary": "✓ t"}]
        append_message(sid, "assistant", "응답", usage_meta=usage, events=events)
        hist = load_history_with_meta(sid)
        assert hist[0]["usage"] == usage
        assert hist[0]["events"] == events


class TestCreateSession:
    def test_returns_uuid_string(self):
        sid = create_session("테스트 세션")
        assert isinstance(sid, str)
        assert len(sid) == 36  # UUID4 형식

    def test_unique_ids(self):
        s1 = create_session()
        s2 = create_session()
        assert s1 != s2

    def test_default_title(self):
        sid = create_session()
        row = get_session(sid)
        assert row is not None
        assert "VEGA" in row["name"]


class TestGetSession:
    def test_get_existing(self):
        sid = create_session("찾기 테스트")
        row = get_session(sid)
        assert row is not None
        assert row["name"] == "찾기 테스트"

    def test_get_nonexistent(self):
        assert get_session("no-such-uuid") is None


class TestListSessions:
    def test_empty_initially(self):
        rows = list_sessions()
        assert rows == []

    def test_lists_after_create(self):
        create_session("S1")
        create_session("S2")
        rows = list_sessions()
        assert len(rows) == 2

    def test_limit_respected(self):
        for i in range(5):
            create_session(f"Session {i}")
        rows = list_sessions(limit=3)
        assert len(rows) == 3


class TestRenameSession:
    def test_rename(self):
        sid = create_session("원래 제목")
        rename_session(sid, "새 제목")
        row = get_session(sid)
        assert row["name"] == "새 제목"


class TestAppendAndLoadHistory:
    def test_append_then_load(self):
        sid = create_session()
        # session_store.py는 role을 'human'/'assistant'로 저장하고 load_history에서 변환
        append_message(sid, "human", "안녕")
        append_message(sid, "assistant", "반가워요")
        history = load_history(sid)
        assert len(history) == 2
        assert history[0]["role"] == "user"  # human → user 변환
        assert history[0]["content"] == "안녕"
        assert history[1]["role"] == "assistant"

    def test_empty_history(self):
        sid = create_session()
        assert load_history(sid) == []

    def test_msg_count_incremented(self):
        sid = create_session()
        append_message(sid, "human", "hi")
        append_message(sid, "human", "hi again")
        row = get_session(sid)
        assert row["msg_count"] == 2


class TestDeleteSession:
    def test_delete_removes_session(self):
        sid = create_session()
        delete_session(sid)
        assert get_session(sid) is None

    def test_delete_cascades_messages(self):
        sid = create_session()
        append_message(sid, "human", "test")
        delete_session(sid)
        assert load_history(sid) == []


class TestGetOrCreateSession:
    def test_none_creates_new(self):
        sid = get_or_create_session(None)
        assert get_session(sid) is not None

    def test_existing_returns_same(self):
        sid = create_session()
        result = get_or_create_session(sid)
        assert result == sid
