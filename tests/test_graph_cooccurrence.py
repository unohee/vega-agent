from __future__ import annotations

import json
import sqlite3

from pipeline.graph_cooccurrence import build_entity_cooccurrence_edges


def _init_event_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            kind TEXT,
            canonical_id TEXT,
            aliases_json TEXT,
            notes TEXT,
            first_seen TEXT,
            last_seen TEXT
        );
        CREATE TABLE event_entities (
            event_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            match_text TEXT
        );
        """
    )
    return conn


def _edge_rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        """
        SELECT source_entity_key, target_entity_key, relation, weight, evidence_json, builder
        FROM entity_cooccurrence_edges
        ORDER BY source_entity_key, target_entity_key
        """
    ).fetchall()]
    conn.close()
    return rows


def test_no_edges_when_source_has_fewer_than_two_distinct_entities(tmp_path):
    db = tmp_path / "agent.db"
    conn = _init_event_db(db)
    conn.execute("INSERT INTO events (event_date, title) VALUES ('2026-06-21', 'solo')")
    conn.execute("INSERT INTO entities (name, kind) VALUES ('Solo', 'person')")
    conn.execute("INSERT INTO event_entities (event_id, entity_id, match_text) VALUES (1, 1, 'Solo')")
    conn.commit()
    conn.close()

    result = build_entity_cooccurrence_edges(db)

    assert result.edges_written == 0
    assert _edge_rows(db) == []


def test_builds_edge_with_event_evidence(tmp_path):
    db = tmp_path / "agent.db"
    conn = _init_event_db(db)
    conn.execute("INSERT INTO events (event_date, title) VALUES ('2026-06-21', 'pair')")
    conn.execute("INSERT INTO entities (name, kind) VALUES ('Alice', 'person')")
    conn.execute("INSERT INTO entities (name, kind) VALUES ('Bob', 'person')")
    conn.execute("INSERT INTO event_entities (event_id, entity_id, match_text) VALUES (1, 1, 'Alice')")
    conn.execute("INSERT INTO event_entities (event_id, entity_id, match_text) VALUES (1, 2, 'Bob')")
    conn.commit()
    conn.close()

    result = build_entity_cooccurrence_edges(db)
    rows = _edge_rows(db)

    assert result.edges_written == 1
    assert rows[0]["source_entity_key"] == "Alice"
    assert rows[0]["target_entity_key"] == "Bob"
    assert rows[0]["weight"] == 1
    assert json.loads(rows[0]["evidence_json"]) == ["event:1"]


def test_uses_non_empty_canonical_id_only(tmp_path):
    db = tmp_path / "agent.db"
    conn = _init_event_db(db)
    conn.execute("INSERT INTO events (event_date, title) VALUES ('2026-06-21', 'canonical')")
    conn.execute("INSERT INTO entities (name, kind, canonical_id) VALUES ('Alice A', 'person', 'alice')")
    conn.execute("INSERT INTO entities (name, kind, canonical_id) VALUES ('Bob B', 'person', '')")
    conn.execute("INSERT INTO event_entities (event_id, entity_id, match_text) VALUES (1, 1, 'Alice')")
    conn.execute("INSERT INTO event_entities (event_id, entity_id, match_text) VALUES (1, 2, 'Bob')")
    conn.commit()
    conn.close()

    build_entity_cooccurrence_edges(db)

    rows = _edge_rows(db)
    assert [(r["source_entity_key"], r["target_entity_key"]) for r in rows] == [("Bob B", "alice")]


def test_rerun_is_idempotent_and_weights_are_deterministic(tmp_path):
    db = tmp_path / "agent.db"
    conn = _init_event_db(db)
    conn.execute("INSERT INTO events (event_date, title) VALUES ('2026-06-21', 'first')")
    conn.execute("INSERT INTO events (event_date, title) VALUES ('2026-06-22', 'second')")
    conn.execute("INSERT INTO entities (name, kind) VALUES ('Alice', 'person')")
    conn.execute("INSERT INTO entities (name, kind) VALUES ('Bob', 'person')")
    conn.executemany(
        "INSERT INTO event_entities (event_id, entity_id, match_text) VALUES (?, ?, ?)",
        [(1, 1, 'Alice'), (1, 2, 'Bob'), (2, 2, 'Bob'), (2, 1, 'Alice')],
    )
    conn.commit()
    conn.close()

    first = build_entity_cooccurrence_edges(db)
    first_rows = _edge_rows(db)
    second = build_entity_cooccurrence_edges(db)
    second_rows = _edge_rows(db)

    assert first == second
    assert first_rows == second_rows
    assert second_rows[0]["weight"] == 2
    assert json.loads(second_rows[0]["evidence_json"]) == ["event:1", "event:2"]


def test_includes_both_message_and_event_sources(tmp_path):
    db = tmp_path / "agent.db"
    conn = _init_event_db(db)
    conn.executescript(
        """
        CREATE TABLE messages (
            uuid TEXT PRIMARY KEY,
            text TEXT NOT NULL
        );
        CREATE TABLE message_entities (
            message_uuid TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            match_text TEXT
        );
        """
    )
    conn.execute("INSERT INTO events (event_date, title) VALUES ('2026-06-21', 'event included')")
    conn.execute("INSERT INTO entities (name, kind) VALUES ('Alice', 'person')")
    conn.execute("INSERT INTO entities (name, kind) VALUES ('Bob', 'person')")
    conn.execute("INSERT INTO event_entities (event_id, entity_id, match_text) VALUES (1, 1, 'Alice')")
    conn.execute("INSERT INTO event_entities (event_id, entity_id, match_text) VALUES (1, 2, 'Bob')")
    conn.execute("INSERT INTO messages (uuid, text) VALUES ('msg-1', 'Alice Bob')")
    conn.execute("INSERT INTO message_entities (message_uuid, entity_id, match_text) VALUES ('msg-1', 1, 'Alice')")
    conn.execute("INSERT INTO message_entities (message_uuid, entity_id, match_text) VALUES ('msg-1', 2, 'Bob')")
    conn.commit()
    conn.close()

    build_entity_cooccurrence_edges(db)

    rows = _edge_rows(db)
    # message 와 event source 를 모두 반영한다 (INT-2236) — 기존엔 message 가 있으면
    # event co-occurrence 가 통째로 누락됐다(이 테스트가 그 버그를 검증하고 있었음).
    evidence = json.loads(rows[0]["evidence_json"])
    assert "message:msg-1" in evidence
    assert "event:1" in evidence
