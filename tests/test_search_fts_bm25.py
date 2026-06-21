# Created: 2026-06-20
# Purpose: smoke-test canonical vega.db FTS/BM25 search over imported corpus rows

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path


def _reload_query(monkeypatch, db_file):
    monkeypatch.setenv("VEGA_DB_FILE", str(db_file))
    import pipeline.data_paths as data_paths

    data_paths.data_dir.cache_clear()

    import pipeline.vega_query as vq

    vq = importlib.reload(vq)
    vq._ensure_schema()
    return vq


def test_fts_bm25_search_returns_historical_imported_rows(tmp_path, monkeypatch):
    db_file = tmp_path / "vega.db"
    vq = _reload_query(monkeypatch, db_file)

    # Representative imported corpus rows: not new chat-session rows.
    old = vq.event_add(
        "2024-01-10",
        "Historical alpha treasury memo",
        "Alpha treasury risk hedge hedge hedge liquidity note from imported archive",
        "finance,imported",
    )["id"]
    newer = vq.event_add(
        "2026-06-15",
        "Fresh session alpha note",
        "Alpha chat session mention only",
        "session",
    )["id"]
    vq.event_add(
        "2024-02-01",
        "무관한 기록",
        "검색 대상이 아닌 본문",
        "imported",
    )

    results = vq.search_events("treasury hedge", limit=5)

    ids = [r["id"] for r in results]
    assert old in ids
    assert newer not in ids
    assert results[0]["id"] == old
    assert "rank" in results[0]


def test_korean_prefix_query_supported_by_unicode61_fts(tmp_path, monkeypatch):
    db_file = tmp_path / "vega.db"
    vq = _reload_query(monkeypatch, db_file)

    old = vq.event_add(
        "2024-03-20",
        "히스토리 한글 토큰",
        "알파벳중요회의 결과와 재무 자동화 검토",
        "imported,korean",
    )["id"]
    vq.event_add("2026-06-15", "신규 세션", "다른 한국어 내용", "session")

    results = vq.search_events("알파벳중요", limit=5)

    assert any(r["id"] == old for r in results)


def test_llm_search_path_uses_vega_db_file_not_legacy_agent_db(tmp_path, monkeypatch):
    canonical = tmp_path / "vega.db"
    legacy = tmp_path / "agent.db"
    vq = _reload_query(monkeypatch, canonical)

    canonical_id = vq.event_add("2024-04-01", "Canonical archive", "canonical sentinel", "imported")["id"]
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, event_date TEXT, title TEXT, body TEXT, tags TEXT)")
        conn.execute("INSERT INTO events VALUES (1, '2026-01-01', 'Legacy agent', 'legacy sentinel', 'legacy')")

    # Avoid importing web.server: this focused DB-path test must not require FastAPI.
    # The LLM slash-command exposure binds pipeline.vega_query.search_events directly.
    server_source = (Path(__file__).parents[1] / "web" / "server.py").read_text(encoding="utf-8")
    assert "from pipeline.vega_query import (" in server_source
    assert "search_events," in server_source
    assert "rows = search_events(args, limit=20)" in server_source

    results = vq.search_events("canonical sentinel", limit=5)

    assert any(r["id"] == canonical_id for r in results)
    assert all(r.get("title") != "Legacy agent" for r in results)
    assert vq._current_db_path() == canonical
