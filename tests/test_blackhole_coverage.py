# Created: 2026-06-15
# Purpose: INT-1527 — 0% 블랙홀 커버리지
#   widgets / settings_store / compaction(실패 시 history 불변) / user_profile / contact_store
# Test Status: green (INT-1527)

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────
# 1. widgets.py
# ─────────────────────────────────────────────

class TestWidgets:
    @pytest.fixture()
    def isolated_widgets(self, tmp_path, monkeypatch):
        wp = tmp_path / "widgets.json"
        import pipeline.widgets as wm
        monkeypatch.setattr(wm, "WIDGETS_PATH", wp)
        return wm

    def test_save_stat_widget(self, isolated_widgets):
        wm = isolated_widgets
        result = wm.save_widget("my-stat", "테스트 위젯", "stat", source="clock")
        assert result["ok"] is True
        assert result["id"] == "my-stat"

    def test_save_text_widget_with_source(self, isolated_widgets):
        wm = isolated_widgets
        result = wm.save_widget("text1", "텍스트", "text", source="session_count")
        assert result["ok"] is True

    def test_invalid_id_rejected(self, isolated_widgets):
        wm = isolated_widgets
        result = wm.save_widget("Invalid ID!", "제목", "stat", source="clock")
        assert result["ok"] is False
        assert "id" in result["error"]

    def test_invalid_type_rejected(self, isolated_widgets):
        wm = isolated_widgets
        result = wm.save_widget("w1", "제목", "invalid_type", source="clock")
        assert result["ok"] is False

    def test_invalid_source_rejected(self, isolated_widgets):
        wm = isolated_widgets
        result = wm.save_widget("w1", "제목", "stat", source="not_in_whitelist")
        assert result["ok"] is False

    def test_duplicate_rejected_without_overwrite(self, isolated_widgets):
        wm = isolated_widgets
        wm.save_widget("w1", "원본", "stat", source="clock")
        result = wm.save_widget("w1", "중복", "stat", source="clock")
        assert result["ok"] is False
        assert "overwrite" in result["error"]

    def test_overwrite_works(self, isolated_widgets):
        wm = isolated_widgets
        wm.save_widget("w1", "원본", "stat", source="clock")
        result = wm.save_widget("w1", "수정본", "stat", source="clock", overwrite=True)
        assert result["ok"] is True

    def test_delete_widget(self, isolated_widgets):
        wm = isolated_widgets
        wm.save_widget("del-me", "삭제 예정", "stat", source="clock")
        result = wm.delete_widget("del-me")
        assert result["ok"] is True
        assert "del-me" not in wm.list_widget_ids()

    def test_delete_nonexistent_fails(self, isolated_widgets):
        wm = isolated_widgets
        result = wm.delete_widget("no-such-widget")
        assert result["ok"] is False

    def test_list_widget_ids(self, isolated_widgets):
        wm = isolated_widgets
        wm.save_widget("a1", "A", "stat", source="clock")
        wm.save_widget("b2", "B", "stat", source="clock")
        ids = wm.list_widget_ids()
        assert "a1" in ids
        assert "b2" in ids

    def test_atomic_write_creates_file(self, isolated_widgets, tmp_path):
        """_save 가 원자적으로 파일 생성 — tmp → replace."""
        wm = isolated_widgets
        wm.save_widget("atom-test", "원자쓰기", "stat", source="clock")
        assert wm.WIDGETS_PATH.exists()
        data = json.loads(wm.WIDGETS_PATH.read_text())
        assert any(w["id"] == "atom-test" for w in data["widgets"])

    def test_text_widget_needs_source_or_text(self, isolated_widgets):
        wm = isolated_widgets
        result = wm.save_widget("t1", "텍스트", "text")  # source=None, text=""
        assert result["ok"] is False


# ─────────────────────────────────────────────
# 2. settings_store.py
# ─────────────────────────────────────────────

class TestSettingsStore:
    @pytest.fixture()
    def isolated_settings(self, tmp_path, monkeypatch):
        sp = tmp_path / "settings.json"
        monkeypatch.setattr("pipeline.settings_store.settings_path", lambda: sp)
        monkeypatch.setattr("pipeline.data_paths.settings_path", lambda: sp)
        import pipeline.settings_store as ss
        return ss, sp

    def test_load_returns_empty_when_no_file(self, isolated_settings):
        ss, _ = isolated_settings
        result = ss.load_settings()
        assert result == {}

    def test_set_and_get_setting(self, isolated_settings):
        ss, _ = isolated_settings
        ss.set_setting("searxng_url", "https://example.com")
        assert ss.get_setting("searxng_url") == "https://example.com"

    def test_atomic_write(self, isolated_settings):
        """_write가 tmp → replace 패턴으로 파일 생성."""
        ss, sp = isolated_settings
        ss.set_setting("key", "value")
        assert sp.exists()
        data = json.loads(sp.read_text())
        assert data["key"] == "value"

    def test_load_returns_empty_on_corrupt_json(self, isolated_settings):
        ss, sp = isolated_settings
        sp.write_text("NOT VALID JSON {{{")
        result = ss.load_settings()
        assert result == {}

    def test_get_setting_default(self, isolated_settings):
        ss, _ = isolated_settings
        result = ss.get_setting("missing_key", default="fallback")
        assert result == "fallback"

    def test_multiple_keys_persist(self, isolated_settings):
        ss, _ = isolated_settings
        ss.set_setting("a", 1)
        ss.set_setting("b", 2)
        data = ss.load_settings()
        assert data["a"] == 1
        assert data["b"] == 2


# ─────────────────────────────────────────────
# 3. compaction.py — LLM 실패 시 history 불변 (INT-1523 수정 검증)
# ─────────────────────────────────────────────

class TestCompactionFailureFallback:
    """INT-1523: LLM 실패 시 to_summarize 손실 없이 원본 history 반환."""

    @pytest.fixture()
    def sample_history(self):
        return [
            {"role": "user", "content": f"메시지 {i}"}
            for i in range(20)
        ]

    @pytest.mark.asyncio
    async def test_llm_failure_returns_original_history(self, sample_history):
        import pipeline.compaction as comp

        with patch.object(comp, "_call_compact_sync", side_effect=RuntimeError("LLM timeout")):
            new_history, summary = await comp.compact_history(sample_history)

        # 원본 히스토리 그대로 반환 — 손실 없음
        assert len(new_history) == len(sample_history)
        assert new_history == sample_history
        assert "컴팩션 실패" in summary

    @pytest.mark.asyncio
    async def test_llm_success_returns_compacted_history(self, sample_history):
        import pipeline.compaction as comp

        mock_summary = "이전 대화 요약 텍스트"

        with patch.object(comp, "_call_compact_sync", return_value=(mock_summary, [])):
            new_history, summary = await comp.compact_history(sample_history)

        # 압축 후 길이 줄어야 함
        assert len(new_history) < len(sample_history)
        assert summary == mock_summary
        # 최근 메시지 보존 확인
        keep = comp._keep_recent()
        for msg in sample_history[-keep:]:
            assert msg in new_history


# ─────────────────────────────────────────────
# 4. user_profile.py
# ─────────────────────────────────────────────

class TestUserProfile:
    @pytest.fixture()
    def isolated_profile(self, tmp_path, monkeypatch):
        pp = tmp_path / "user_profile.json"
        monkeypatch.setattr("pipeline.user_profile.user_profile_path", lambda: pp)
        monkeypatch.setattr("pipeline.data_paths.user_profile_path", lambda: pp)
        import pipeline.user_profile as up
        return up, pp

    def test_load_returns_default_when_no_file(self, isolated_profile):
        up, _ = isolated_profile
        profile = up.load_profile()
        assert profile["onboarded"] is False
        assert profile["display_name"] == "사용자"

    def test_save_sets_onboarded_true(self, isolated_profile):
        up, _ = isolated_profile
        up.save_profile({"display_name": "테스트유저"})
        profile = up.load_profile()
        assert profile["onboarded"] is True
        assert profile["display_name"] == "테스트유저"

    def test_is_onboarded_false_before_save(self, isolated_profile):
        up, _ = isolated_profile
        assert up.is_onboarded() is False

    def test_is_onboarded_true_after_save(self, isolated_profile):
        up, _ = isolated_profile
        up.save_profile({"display_name": "유저"})
        assert up.is_onboarded() is True

    def test_load_returns_default_on_corrupt_json(self, isolated_profile):
        up, pp = isolated_profile
        pp.write_text("INVALID JSON !!!")
        profile = up.load_profile()
        assert profile["onboarded"] is False

    def test_display_name_fallback(self, isolated_profile):
        up, _ = isolated_profile
        # 파일 없음 → 기본값
        assert up.display_name() == "사용자"

    def test_email_accounts_empty_default(self, isolated_profile):
        up, _ = isolated_profile
        assert up.email_accounts() == []

    def test_atomic_write(self, isolated_profile):
        """save_profile이 tmp → replace로 파일 생성."""
        up, pp = isolated_profile
        up.save_profile({"display_name": "원자쓰기 테스트"})
        assert pp.exists()
        data = json.loads(pp.read_text())
        assert data["onboarded"] is True


# ─────────────────────────────────────────────
# 5. contact_store.py — update_memo 앵커 수정 (INT-1523)
# ─────────────────────────────────────────────

class TestContactStoreMemo:
    """INT-1523: update_memo가 정확 일치만 업데이트 (동명 접두사 오염 방지)."""

    @pytest.fixture()
    def contacts_db(self, tmp_path, monkeypatch):
        db_file = tmp_path / "contacts.db"
        import pipeline.contact_store as cs
        monkeypatch.setattr(cs, "_DB_PATH", db_file)

        # 테스트용 DB 초기화
        con = sqlite3.connect(str(db_file))
        cs.init_schema(con)
        con.execute("INSERT INTO contacts(name, memo) VALUES ('이슬', '')")
        con.execute("INSERT INTO contacts(name, memo) VALUES ('이슬기', '')")
        con.execute("INSERT INTO contacts(name, memo) VALUES ('이슬아', '')")
        con.commit()
        con.close()
        return cs, db_file

    def test_exact_match_updates_only_target(self, contacts_db):
        cs, db_file = contacts_db
        result = cs.update_memo("이슬", "새로운 메모")
        assert result is True

        con = sqlite3.connect(str(db_file))
        rows = {r[0]: r[1] for r in con.execute("SELECT name, memo FROM contacts").fetchall()}
        con.close()
        assert rows["이슬"] == "새로운 메모"
        assert rows["이슬기"] == ""     # 오염 없음
        assert rows["이슬아"] == ""     # 오염 없음

    def test_nonexistent_name_returns_false(self, contacts_db):
        cs, _ = contacts_db
        result = cs.update_memo("존재안함", "메모")
        assert result is False
