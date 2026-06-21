from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from pipeline import imessage_importer


SOURCE_GUID = "iMessage;-;+15551234567;fixture-guid"
APPLE_TIMESTAMP_SECONDS = 60
EXPECTED_TIMESTAMP = "2001-01-01T00:01:00+00:00"


def _create_source_chat_db(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE handle (
                ROWID INTEGER PRIMARY KEY,
                id TEXT
            );
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY,
                guid TEXT,
                text TEXT,
                date INTEGER,
                is_from_me INTEGER,
                handle_id INTEGER
            );
            CREATE TABLE chat (
                ROWID INTEGER PRIMARY KEY,
                chat_identifier TEXT
            );
            CREATE TABLE chat_message_join (
                chat_id INTEGER,
                message_id INTEGER
            );
            """
        )
        con.execute("INSERT INTO handle (ROWID, id) VALUES (?, ?)", (1, "+15551234567"))
        con.execute("INSERT INTO chat (ROWID, chat_identifier) VALUES (?, ?)", (1, "chat-fixture"))
        con.execute(
            """
            INSERT INTO message (ROWID, guid, text, date, is_from_me, handle_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (42, SOURCE_GUID, "fixture text", APPLE_TIMESTAMP_SECONDS, 0, 1),
        )
        con.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)", (1, 42))


def _fetch_imported_row(dest_db: Path) -> sqlite3.Row:
    con = sqlite3.connect(dest_db)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM imessage_messages").fetchone()
        assert row is not None
        return row
    finally:
        con.close()


def _import_count(dest_db: Path) -> int:
    with sqlite3.connect(dest_db) as con:
        return con.execute("SELECT COUNT(*) FROM imessage_messages").fetchone()[0]


def test_import_imessages_imports_expected_fields_and_stable_external_id(tmp_path: Path) -> None:
    source_db = tmp_path / "chat.db"
    dest_db = tmp_path / "vega.db"
    _create_source_chat_db(source_db)

    result = imessage_importer.import_imessages(source_db=source_db, dest_db=dest_db)

    expected_external_id = "imessage:" + hashlib.sha256(SOURCE_GUID.encode("utf-8")).hexdigest()
    row = _fetch_imported_row(dest_db)
    assert result.imported == 1
    assert result.skipped == 0
    assert row["sender"] == "+15551234567"
    assert row["timestamp"] == EXPECTED_TIMESTAMP
    assert row["chat_identifier"] == "chat-fixture"
    assert row["text"] == "fixture text"
    assert row["external_id"] == expected_external_id


def test_import_imessages_rerun_dedupes_by_external_id(tmp_path: Path) -> None:
    source_db = tmp_path / "chat.db"
    dest_db = tmp_path / "vega.db"
    _create_source_chat_db(source_db)

    first = imessage_importer.import_imessages(source_db=source_db, dest_db=dest_db)
    second = imessage_importer.import_imessages(source_db=source_db, dest_db=dest_db)

    assert first.imported == 1
    assert second.imported == 0
    assert second.skipped == 1
    assert _import_count(dest_db) == 1


def test_import_imessages_surfaces_permission_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_db = tmp_path / "chat.db"
    dest_db = tmp_path / "vega.db"

    def deny_source_access(_source_path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("permission denied")

    monkeypatch.setattr(imessage_importer, "_connect_source", deny_source_access)

    with pytest.raises(imessage_importer.IMessageImportPermissionError, match="permission denied"):
        imessage_importer.import_imessages(source_db=source_db, dest_db=dest_db)

    assert not dest_db.exists()
