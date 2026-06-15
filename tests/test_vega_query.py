# Created: 2026-06-15
# Purpose: INT-1525 — vega_query.py DB 스키마 + 메모리 쓰기 회귀 테스트
#   신규 DB 스키마 정합성, ALTER TABLE 마이그레이션 idempotent, CRUD 경로
# Test Status: green (INT-1525)

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """격리된 agent.db — 실 DB 오염 없이 테스트."""
    db_file = tmp_path / "agent.db"

    def _fake_db_path():
        return db_file

    monkeypatch.setattr("pipeline.vega_query.DB_PATH", db_file)
    monkeypatch.setattr("pipeline.data_paths.db_path", _fake_db_path)

    import importlib
    import pipeline.vega_query as vq
    vq.DB_PATH = db_file
    # 모듈 레벨 _conn 이 DB_PATH 를 직접 참조하므로 패치 후 _ensure_schema 재실행
    vq._ensure_schema()
    return db_file


# ─────────────────────────────────────────────
# 1. 신규 DB 스키마 정합성
# ─────────────────────────────────────────────

class TestEnsureSchema:
    def test_persona_sections_has_source_column(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(persona_sections)").fetchall()}
        conn.close()
        assert "source" in cols
        assert "ingested_at" in cols

    def test_events_has_date_raw_era_ingested_at(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
        conn.close()
        assert "date_raw" in cols
        assert "era" in cols
        assert "ingested_at" in cols

    def test_entities_table_exists(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "entities" in tables

    def test_ensure_schema_idempotent(self, tmp_db):
        """두 번 호출해도 에러 없음."""
        import pipeline.vega_query as vq
        vq._ensure_schema()
        vq._ensure_schema()  # no exception


# ─────────────────────────────────────────────
# 2. 기존 DB 마이그레이션 — ALTER TABLE idempotent
# ─────────────────────────────────────────────

class TestMigration:
    def test_adds_missing_columns_to_old_schema(self, tmp_db):
        """구 스키마(source/ingested_at 없는) DB에 _ensure_schema 호출 → 컬럼 추가됨."""
        conn = sqlite3.connect(str(tmp_db))
        # 강제로 구 스키마 흉내 — source/ingested_at 제거
        conn.execute("ALTER TABLE persona_sections RENAME TO persona_sections_old")
        conn.execute("""
            CREATE TABLE persona_sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                section_key TEXT NOT NULL,
                content TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'global',
                version INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("INSERT INTO persona_sections(section_key,content) VALUES ('test','old data')")
        conn.commit()
        conn.close()

        import pipeline.vega_query as vq
        vq._ensure_schema()

        conn2 = sqlite3.connect(str(tmp_db))
        cols = {r[1] for r in conn2.execute("PRAGMA table_info(persona_sections)").fetchall()}
        row = conn2.execute("SELECT content FROM persona_sections WHERE section_key='test'").fetchone()
        conn2.close()

        assert "source" in cols or "ingested_at" in cols  # 마이그레이션 컬럼 추가됨
        # 기존 데이터 보존
        assert row is not None
        assert row[0] == "old data"


# ─────────────────────────────────────────────
# 3. persona_upsert — 버전 관리
# ─────────────────────────────────────────────

class TestPersonaUpsert:
    def test_insert_new_section(self, tmp_db):
        import pipeline.vega_query as vq
        result = vq.persona_upsert("identity", "나는 VEGA입니다")
        assert result["ok"] is True
        assert result["version"] == 1
        assert result["section_key"] == "identity"

    def test_version_increments(self, tmp_db):
        import pipeline.vega_query as vq
        vq.persona_upsert("identity", "v1 내용")
        result2 = vq.persona_upsert("identity", "v2 내용")
        assert result2["version"] == 2

    def test_old_version_deactivated(self, tmp_db):
        import pipeline.vega_query as vq
        vq.persona_upsert("identity", "v1")
        vq.persona_upsert("identity", "v2")
        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute(
            "SELECT version, is_active FROM persona_sections WHERE section_key='identity' ORDER BY version"
        ).fetchall()
        conn.close()
        assert rows[0][1] == 0  # v1 비활성
        assert rows[1][1] == 1  # v2 활성

    def test_get_persona_returns_active(self, tmp_db):
        import pipeline.vega_query as vq
        vq.persona_upsert("identity", "v1")
        vq.persona_upsert("identity", "v2 최신")
        content = vq.get_persona("identity")
        assert "v2 최신" in content
        assert "v1" not in content


# ─────────────────────────────────────────────
# 4. event_add + 읽기 경로
# ─────────────────────────────────────────────

class TestEventAdd:
    def test_insert_event(self, tmp_db):
        import pipeline.vega_query as vq
        result = vq.event_add("2026-06-15", "테스트 이벤트", "본문 내용", "테스트")
        assert result["ok"] is True
        assert isinstance(result["id"], int)

    def test_events_by_date_range(self, tmp_db):
        import pipeline.vega_query as vq
        vq.event_add("2026-06-10", "이벤트A", "내용A", "tag")
        vq.event_add("2026-06-20", "이벤트B", "내용B", "tag")
        vq.event_add("2026-07-01", "이벤트C", "내용C", "tag")  # 범위 밖

        results = vq.events_by_date("2026-06-01", "2026-06-30")
        titles = [r["title"] for r in results]
        assert "이벤트A" in titles
        assert "이벤트B" in titles
        assert "이벤트C" not in titles

    def test_events_by_tag(self, tmp_db):
        import pipeline.vega_query as vq
        vq.event_add("2026-06-15", "음악 이벤트", "내용", "music,audio")
        vq.event_add("2026-06-15", "트레이딩", "내용", "trading")
        results = vq.events_by_tag("music")
        assert any(r["title"] == "음악 이벤트" for r in results)
        assert all(r["title"] != "트레이딩" for r in results)

    def test_search_events_by_keyword(self, tmp_db):
        import pipeline.vega_query as vq
        vq.event_add("2026-06-15", "알파벳 중요 회의", "회의 내용 기록", "business")
        vq.event_add("2026-06-15", "무관한 이벤트", "다른 내용", "personal")
        results = vq.search_events("알파벳")
        assert any(r["title"] == "알파벳 중요 회의" for r in results)
        assert all(r["title"] != "무관한 이벤트" for r in results)

    def test_event_date_raw_era_stored(self, tmp_db):
        """date_raw, era 컬럼이 실제로 저장됨 — INT-1521 수정 검증."""
        import pipeline.vega_query as vq
        vq.event_add("2026-06-15", "스키마 테스트", "내용", "test")
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT date_raw, era FROM events WHERE title='스키마 테스트'").fetchone()
        conn.close()
        assert row[0] == "2026-06-15"   # date_raw
        assert row[1] == "2026-06"      # era = YYYY-MM


# ─────────────────────────────────────────────
# 5. entity_upsert + 읽기
# ─────────────────────────────────────────────

class TestEntityUpsert:
    def test_create_entity(self, tmp_db):
        import pipeline.vega_query as vq
        result = vq.entity_upsert("이슬", "person")
        assert result["ok"] is True
        assert result["action"] == "created"

    def test_update_entity(self, tmp_db):
        import pipeline.vega_query as vq
        vq.entity_upsert("이슬", "person")
        result2 = vq.entity_upsert("이슬", "person", notes="업데이트된 메모")
        assert result2["action"] == "updated"

    def test_get_entity(self, tmp_db):
        import pipeline.vega_query as vq
        vq.entity_upsert("이슬", "person", notes="테스트 메모")
        entity = vq.get_entity("이슬")
        assert entity is not None
        assert entity["name"] == "이슬"
        assert entity["kind"] == "person"

    def test_get_missing_entity_returns_none(self, tmp_db):
        import pipeline.vega_query as vq
        result = vq.get_entity("존재하지않는엔티티")
        assert result is None
