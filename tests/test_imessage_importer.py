from __future__ import annotations

import sqlite3

import pytest

from pipeline.imessage_importer import (
    FULL_DISK_ACCESS_HELP,
    IMessageImportError,
    import_imessages,
)


def _create_chat_db(path):
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
            VALUES (42, 'msg-guid-42', 1000000000, 'hello', 0, 1)
            """
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (7, 42)")


def test_import_imessages_imports_stable_external_id_and_dedupes(tmp_path, monkeypatch):
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
    assert second.scanned == 1
    assert second.inserted == 0

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


def test_import_imessages_missing_source_has_setup_instructions(tmp_path):
    missing = tmp_path / "missing-chat.db"

    with pytest.raises(IMessageImportError) as exc:
        import_imessages(source_db=missing, destination_db=tmp_path / "vega.db")

    message = str(exc.value)
    assert FULL_DISK_ACCESS_HELP in message
    assert "Missing source database" in message


def test_import_imessages_sqlite_open_failure_has_setup_instructions(tmp_path, monkeypatch):
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
