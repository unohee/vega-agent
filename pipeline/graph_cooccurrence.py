# Created: 2026-06-21
# Purpose: Build derived entity co-occurrence graph edges from existing source/entity links.
# Dependencies: sqlite3 (stdlib)

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from pipeline.data_paths import db_path as _db_path

BUILDER = "entity_cooccurrence_v1"
RELATION = "cooccurs"


@dataclass(frozen=True)
class BuildResult:
    sources_scanned: int
    edges_written: int


@dataclass(frozen=True)
class EntityRef:
    key: str
    name: str


@dataclass(frozen=True)
class SourceEntities:
    source_type: str
    source_id: str
    entities: tuple[EntityRef, ...]


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or _db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_entity_edge_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_edges (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity_key TEXT NOT NULL,
            target_entity_key TEXT NOT NULL,
            relation          TEXT NOT NULL DEFAULT 'cooccurs',
            weight            INTEGER NOT NULL,
            evidence_json     TEXT NOT NULL,
            builder           TEXT NOT NULL,
            updated_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_entity_key, target_entity_key, relation, builder)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entity_edges_builder_weight
        ON entity_edges(builder, weight DESC)
        """
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _entity_key(row: sqlite3.Row) -> str:
    canonical = (row["canonical_id"] or "").strip()
    if canonical:
        return canonical
    name = (row["name"] or "").strip()
    return name or str(row["entity_row_id"])


def _dedupe_entities(rows: list[sqlite3.Row]) -> tuple[EntityRef, ...]:
    by_key: dict[str, EntityRef] = {}
    for row in rows:
        key = _entity_key(row)
        if key not in by_key:
            by_key[key] = EntityRef(key=key, name=row["name"] or key)
    return tuple(by_key[k] for k in sorted(by_key))


def _message_source_id(row: sqlite3.Row) -> str:
    if "uuid" in row.keys() and row["uuid"]:
        return str(row["uuid"])
    return str(row["id"])


def _iter_message_sources(conn: sqlite3.Connection) -> list[SourceEntities]:
    if not (_table_exists(conn, "message_entities") and _table_exists(conn, "messages")):
        return []
    link_cols = _columns(conn, "message_entities")
    message_col = "message_uuid" if "message_uuid" in link_cols else "message_id" if "message_id" in link_cols else "msg_id" if "msg_id" in link_cols else ""
    if not message_col or "entity_id" not in link_cols:
        return []

    messages_cols = _columns(conn, "messages")
    join_col = "uuid" if message_col.endswith("uuid") and "uuid" in messages_cols else "id" if "id" in messages_cols else "uuid"
    rows = conn.execute(
        f"""
        SELECT m.*, e.id AS entity_row_id, e.name, e.canonical_id
        FROM messages m
        JOIN message_entities me ON me.{message_col} = m.{join_col}
        JOIN entities e ON e.id = me.entity_id
        ORDER BY m.{join_col}, e.id
        """
    ).fetchall()

    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    source_rows: dict[str, sqlite3.Row] = {}
    for row in rows:
        source_id = _message_source_id(row)
        grouped[source_id].append(row)
        source_rows[source_id] = row
    return [
        SourceEntities("message", source_id, _dedupe_entities(entity_rows))
        for source_id, entity_rows in sorted(grouped.items())
        if len(_dedupe_entities(entity_rows)) >= 2 and source_rows[source_id] is not None
    ]


def _iter_event_sources(conn: sqlite3.Connection) -> list[SourceEntities]:
    if not (_table_exists(conn, "event_entities") and _table_exists(conn, "events")):
        return []
    link_cols = _columns(conn, "event_entities")
    if "event_id" not in link_cols or "entity_id" not in link_cols:
        return []
    rows = conn.execute(
        """
        SELECT ev.id AS source_id, e.id AS entity_row_id, e.name, e.canonical_id
        FROM events ev
        JOIN event_entities ee ON ee.event_id = ev.id
        JOIN entities e ON e.id = ee.entity_id
        ORDER BY ev.id, e.id
        """
    ).fetchall()

    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["source_id"])].append(row)
    return [
        SourceEntities("event", source_id, _dedupe_entities(entity_rows))
        for source_id, entity_rows in sorted(grouped.items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0])
        if len(_dedupe_entities(entity_rows)) >= 2
    ]


def _load_sources(conn: sqlite3.Connection) -> list[SourceEntities]:
    message_sources = _iter_message_sources(conn)
    if message_sources:
        return message_sources
    return _iter_event_sources(conn)


def build_entity_cooccurrence_edges(db_path: str | Path | None = None) -> BuildResult:
    """Recompute deterministic weighted co-occurrence edges for the configured DB."""
    with connect(db_path) as conn:
        ensure_entity_edge_schema(conn)
        sources = _load_sources(conn)
        pair_evidence: dict[tuple[str, str], set[str]] = defaultdict(set)

        for source in sources:
            refs = source.entities
            for left, right in combinations(refs, 2):
                a, b = sorted((left.key, right.key))
                pair_evidence[(a, b)].add(f"{source.source_type}:{source.source_id}")

        rows = [
            (a, b, RELATION, len(evidence), json.dumps(sorted(evidence), ensure_ascii=False), BUILDER)
            for (a, b), evidence in sorted(pair_evidence.items())
        ]
        current_keys = {(a, b) for a, b, *_ in rows}

        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT source_entity_key, target_entity_key
            FROM entity_edges
            WHERE builder=? AND relation=?
            """,
            (BUILDER, RELATION),
        ).fetchall()
        stale = [
            (r["source_entity_key"], r["target_entity_key"], RELATION, BUILDER)
            for r in existing
            if (r["source_entity_key"], r["target_entity_key"]) not in current_keys
        ]
        conn.executemany(
            """
            DELETE FROM entity_edges
            WHERE source_entity_key=? AND target_entity_key=? AND relation=? AND builder=?
            """,
            stale,
        )
        conn.executemany(
            """
            INSERT INTO entity_edges (
                source_entity_key, target_entity_key, relation, weight, evidence_json, builder
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_entity_key, target_entity_key, relation, builder)
            DO UPDATE SET
                weight=excluded.weight,
                evidence_json=excluded.evidence_json,
                updated_at=CURRENT_TIMESTAMP
            WHERE entity_edges.weight <> excluded.weight
               OR entity_edges.evidence_json <> excluded.evidence_json
            """,
            rows,
        )
        conn.commit()
        return BuildResult(sources_scanned=len(sources), edges_written=len(rows))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build entity co-occurrence graph edges")
    parser.add_argument("--db", type=Path, default=None, help="SQLite DB path; defaults to VEGA agent DB")
    args = parser.parse_args(argv)
    result = build_entity_cooccurrence_edges(args.db)
    print(f"entity cooccurrence: sources={result.sources_scanned} edges={result.edges_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
