from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from pipeline.local_file_crawl import crawl_local_files


def _rows(db_file: Path, table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


def test_crawl_ingests_once_then_skips_unchanged(tmp_path, monkeypatch):
    db_file = tmp_path / "vega.db"
    root = tmp_path / "docs"
    root.mkdir()
    (root / "note.md").write_text("# Note\nhello\n", encoding="utf-8")
    (root / "ignore.pdf").write_bytes(b"%PDF")

    monkeypatch.setenv("VEGA_DB_FILE", str(db_file))

    first = crawl_local_files([root])
    assert first["ingested"] == 1
    assert first["updated"] == 0
    assert first["skipped"] == 1
    assert first["errors"] == []

    messages = _rows(db_file, "messages")
    checkpoints = _rows(db_file, "local_file_checkpoints")
    assert len(messages) == 1
    assert len(checkpoints) == 1
    assert messages[0]["source"] == "local_file"
    assert messages[0]["sender"] == "document"
    assert messages[0]["text"] == "# Note\nhello\n"
    assert checkpoints[0]["absolute_path"] == str((root / "note.md").resolve())
    first_hash = checkpoints[0]["sha256"]

    second = crawl_local_files([root])
    assert second["ingested"] == 0
    assert second["updated"] == 0
    assert second["skipped"] == 2
    assert second["errors"] == []
    assert len(_rows(db_file, "messages")) == 1
    assert len(_rows(db_file, "local_file_checkpoints")) == 1
    assert _rows(db_file, "local_file_checkpoints")[0]["sha256"] == first_hash


def test_crawl_updates_existing_file_without_duplicate_message(tmp_path, monkeypatch):
    db_file = tmp_path / "vega.db"
    root = tmp_path / "docs"
    root.mkdir()
    note = root / "note.txt"
    note.write_text("before\n", encoding="utf-8")
    monkeypatch.setenv("VEGA_DB_FILE", str(db_file))

    first = crawl_local_files([root])
    first_checkpoint = _rows(db_file, "local_file_checkpoints")[0]
    first_message = _rows(db_file, "messages")[0]

    note.write_text("after\n", encoding="utf-8")
    os.utime(note, ns=(first_checkpoint["mtime_ns"] + 1_000_000_000, first_checkpoint["mtime_ns"] + 1_000_000_000))

    second = crawl_local_files([root])
    assert first["ingested"] == 1
    assert second["ingested"] == 0
    assert second["updated"] == 1
    assert second["skipped"] == 0
    assert second["errors"] == []

    messages = _rows(db_file, "messages")
    checkpoints = _rows(db_file, "local_file_checkpoints")
    assert len(messages) == 1
    assert len(checkpoints) == 1
    assert messages[0]["uuid"] == first_message["uuid"]
    assert messages[0]["text"] == "after\n"
    assert checkpoints[0]["sha256"] != first_checkpoint["sha256"]
    assert checkpoints[0]["mtime_ns"] != first_checkpoint["mtime_ns"]


def test_crawl_reports_decode_error_for_eligible_binary(tmp_path, monkeypatch):
    db_file = tmp_path / "vega.db"
    root = tmp_path / "docs"
    root.mkdir()
    (root / "bad.md").write_bytes(b"ok\xff")
    monkeypatch.setenv("VEGA_DB_FILE", str(db_file))

    result = crawl_local_files([root])

    assert result["ingested"] == 0
    assert result["updated"] == 0
    assert result["skipped"] == 1
    assert result["errors"]
    assert result["errors"][0]["code"] == "decode_error"
    assert _rows(db_file, "messages") == []
    assert _rows(db_file, "local_file_checkpoints") == []
