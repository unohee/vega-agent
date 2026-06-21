from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline import browser_history_connector as connector


CHROME_EPOCH_OFFSET_US = 11_644_473_600 * 1_000_000


def chrome_time(unix_seconds: int) -> int:
    return CHROME_EPOCH_OFFSET_US + unix_seconds * 1_000_000


@pytest.fixture()
def source_history_db(tmp_path: Path) -> Path:
    path = tmp_path / "History"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT NOT NULL, title TEXT)")
        conn.execute(
            """
            CREATE TABLE visits (
                id INTEGER PRIMARY KEY,
                url INTEGER NOT NULL,
                visit_time INTEGER NOT NULL
            )
            """
        )
        conn.execute("INSERT INTO urls (id, url, title) VALUES (1, 'https://example.com/', 'Example')")
        conn.execute("INSERT INTO urls (id, url, title) VALUES (2, 'https://vega.local/', 'VEGA')")
        conn.execute("INSERT INTO visits (id, url, visit_time) VALUES (10, 1, ?)", (chrome_time(1_700_000_000),))
    return path


@pytest.fixture()
def destination_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "vega.db"
    monkeypatch.setenv("VEGA_DB_FILE", str(path))
    return path


def read_imported_visits(destination_db: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(destination_db) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT url, title, visit_timestamp, source_profile, chrome_visit_id, chrome_visit_time
            FROM browser_history_visits
            ORDER BY chrome_visit_time, chrome_visit_id
            """
        ).fetchall()


def read_checkpoint(destination_db: Path, source_history_db: Path) -> sqlite3.Row:
    with sqlite3.connect(destination_db) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT last_visit_time, last_visit_id
            FROM browser_history_checkpoints
            WHERE source_profile = ?
            """,
            (str(source_history_db),),
        ).fetchone()


def add_visit(source_history_db: Path, visit_id: int, url_id: int, visit_time: int) -> None:
    with sqlite3.connect(source_history_db) as conn:
        conn.execute("INSERT INTO visits (id, url, visit_time) VALUES (?, ?, ?)", (visit_id, url_id, visit_time))


def test_initial_import_copies_source_and_stores_visit(
    source_history_db: Path,
    destination_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened_paths: list[Path] = []
    original_connect_readonly = connector._connect_readonly

    def spy_connect_readonly(path: Path) -> sqlite3.Connection:
        opened_paths.append(path)
        assert path != source_history_db
        assert path.exists()
        return original_connect_readonly(path)

    monkeypatch.setattr(connector, "_connect_readonly", spy_connect_readonly)

    result = connector.import_recent_visits(source_history_db)

    assert result["source_rows"] == 1
    assert result["inserted"] == 1
    assert len(opened_paths) == 1
    assert not opened_paths[0].exists()

    rows = read_imported_visits(destination_db)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.com/"
    assert rows[0]["title"] == "Example"
    assert rows[0]["source_profile"] == str(source_history_db)
    assert rows[0]["chrome_visit_id"] == 10
    assert rows[0]["visit_timestamp"] == datetime.fromtimestamp(1_700_000_000, UTC).isoformat()

    checkpoint = read_checkpoint(destination_db, source_history_db)
    assert checkpoint["last_visit_time"] == chrome_time(1_700_000_000)
    assert checkpoint["last_visit_id"] == 10


def test_duplicate_rerun_imports_zero(source_history_db: Path, destination_db: Path) -> None:
    first = connector.import_recent_visits(source_history_db)
    second = connector.import_recent_visits(source_history_db)

    assert first["inserted"] == 1
    assert second["source_rows"] == 0
    assert second["inserted"] == 0
    assert len(read_imported_visits(destination_db)) == 1


def test_incremental_import_only_newer_visit(source_history_db: Path, destination_db: Path) -> None:
    connector.import_recent_visits(source_history_db)
    older_same_checkpoint_time = chrome_time(1_700_000_000)
    newer_time = chrome_time(1_700_000_100)
    add_visit(source_history_db, 9, 2, older_same_checkpoint_time)
    add_visit(source_history_db, 11, 2, newer_time)

    result = connector.import_recent_visits(source_history_db)

    assert result["source_rows"] == 1
    assert result["inserted"] == 1

    rows = read_imported_visits(destination_db)
    assert [row["chrome_visit_id"] for row in rows] == [10, 11]
    assert rows[1]["url"] == "https://vega.local/"
    assert rows[1]["title"] == "VEGA"
    assert rows[1]["visit_timestamp"] == datetime.fromtimestamp(1_700_000_100, UTC).isoformat()

    checkpoint = read_checkpoint(destination_db, source_history_db)
    assert checkpoint["last_visit_time"] == newer_time
    assert checkpoint["last_visit_id"] == 11


def test_env_configured_profile_path(source_history_db: Path, destination_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEGA_BROWSER_HISTORY_DB", str(source_history_db))

    result = connector.import_recent_visits()

    assert result["inserted"] == 1
    assert len(read_imported_visits(destination_db)) == 1
