from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pipeline import imessage_importer
from pipeline.imessage_importer import (
    FULL_DISK_ACCESS_HELP,
    IMessageImportError,
    import_imessages,
)


SOURCE_GUID = "iMessage;-;+15551234567;fixture-guid"
APPLE_TIMESTAMP_SECONDS = 60
EXPECTED_TIMESTAMP = "2001-01-01T00:01:00+00:00"


def _create_chat_db(path: Path, *, guid: str = "msg-guid-42", text: str = "hello", date: int = 1_000_000_000) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY,
                guid TEXT,
                date INTEGER,
                text TEXT,
                is_from_me INTEGER,
                handle_id INTEGER
            );
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            """
        )
        conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551234567')")
        conn.execute("INSERT INTO chat (ROWID, chat_identifier) VALUES (7, 'chat-abc')")
        conn.execute(
            """
            INSERT INTO message (ROWID, guid, date, text, is_from_me, handle_id)
            VALUES (42, ?, ?, ?, 0, 1)
            """,
            (guid, date, text),
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (7, 42)")


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


def test_import_imessages_imports_stable_external_id_and_dedupes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "chat.db"
    destination_dir = tmp_path / "vega-data"
    destination = destination_dir / "vega.db"
    _create_chat_db(source)

    monkeypatch.setenv("VEGA_DB_FILE", str(destination))
    monkeypatch.setenv("VEGA_DATA_DIR", str(destination_dir))
    from pipeline import data_paths

    data_paths.data_dir.cache_clear()

    first = import_imessages(source_db=source)
    second = import_imessages(source_db=source)

    assert first.scanned == 1
    assert first.inserted == 1
    assert first.imported == 1
    assert first.skipped == 0
    assert second.scanned == 1
    assert second.inserted == 0
    assert second.imported == 0
    assert second.skipped == 1

    with sqlite3.connect(str(destination)) as conn:
        rows = conn.execute(
            """
            SELECT external_id, source, sender, timestamp, chat_identifier, text
            FROM imessage_messages
            """
        ).fetchall()

    assert rows == [
        (
            "imessage:42:7:msg-guid-42",
            "imessage",
            "+15551234567",
            "2032-09-09T01:46:40+00:00",
            "chat-abc",
            "hello",
        )
    ]


def test_import_imessages_imports_expected_fields_with_dest_db_alias(tmp_path: Path) -> None:
    source_db = tmp_path / "chat.db"
    dest_db = tmp_path / "vega.db"
    _create_chat_db(source_db, guid=SOURCE_GUID, text="fixture text", date=APPLE_TIMESTAMP_SECONDS)

    result = imessage_importer.import_imessages(source_db=source_db, dest_db=dest_db)

    row = _fetch_imported_row(dest_db)
    assert result.imported == 1
    assert result.skipped == 0
    assert row["sender"] == "+15551234567"
    assert row["timestamp"] == EXPECTED_TIMESTAMP
    assert row["chat_identifier"] == "chat-abc"
    assert row["text"] == "fixture text"
    assert row["external_id"] == f"imessage:42:7:{SOURCE_GUID}"
    assert row["is_from_me"] == 0
    assert row["source_guid"] == SOURCE_GUID
    assert row["source_rowid"] == 42


def test_import_imessages_rerun_dedupes_by_external_id(tmp_path: Path) -> None:
    source_db = tmp_path / "chat.db"
    dest_db = tmp_path / "vega.db"
    _create_chat_db(source_db, guid=SOURCE_GUID, text="fixture text", date=APPLE_TIMESTAMP_SECONDS)

    first = imessage_importer.import_imessages(source_db=source_db, dest_db=dest_db)
    second = imessage_importer.import_imessages(source_db=source_db, dest_db=dest_db)

    assert first.imported == 1
    assert second.imported == 0
    assert second.skipped == 1
    assert _import_count(dest_db) == 1


def test_import_imessages_missing_source_has_setup_instructions(tmp_path: Path) -> None:
    missing = tmp_path / "missing-chat.db"

    with pytest.raises(IMessageImportError) as exc:
        import_imessages(source_db=missing, destination_db=tmp_path / "vega.db")

    message = str(exc.value)
    assert FULL_DISK_ACCESS_HELP in message
    assert "Missing source database" in message


def test_import_imessages_sqlite_open_failure_has_setup_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "chat.db"
    source.write_text("not sqlite")

    real_connect = sqlite3.connect

    def deny_connect(database, *args, **kwargs):
        if str(database).startswith("file:"):
            raise sqlite3.OperationalError("authorization denied")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", deny_connect)

    with pytest.raises(IMessageImportError) as exc:
        import_imessages(source_db=source, destination_db=tmp_path / "vega.db")

    message = str(exc.value)
    assert FULL_DISK_ACCESS_HELP in message
    assert "authorization denied" in message


def test_import_imessages_surfaces_permission_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_db = tmp_path / "chat.db"
    dest_db = tmp_path / "vega.db"

    def deny_source_access(_source_path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("permission denied")

    monkeypatch.setattr(imessage_importer, "_connect_source", deny_source_access)

    with pytest.raises(imessage_importer.IMessageImportPermissionError, match="permission denied"):
        imessage_importer.import_imessages(source_db=source_db, dest_db=dest_db)

    assert not dest_db.exists()
