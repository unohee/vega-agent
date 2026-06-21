from __future__ import annotations

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pipeline.sync_health as sync_health
from web.routers import onboarding


def _mock_google(monkeypatch, authenticated: bool = True) -> None:
    from pipeline.auth import google

    monkeypatch.setattr(google, "is_configured", lambda: True)
    monkeypatch.setattr(google, "is_authenticated", lambda: authenticated)
    monkeypatch.setattr(google, "stored_email", lambda: "a@x.com" if authenticated else None)
    monkeypatch.setattr(
        google,
        "stored_accounts",
        lambda: ([{"email": "a@x.com", "is_default": True}] if authenticated else []),
    )
    monkeypatch.setattr(google, "client_source", lambda: "builtin")


def test_sync_status_reports_cursor_and_checkpoint_rows(tmp_path, monkeypatch):
    db = tmp_path / "agent.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE google_sync_cursors (
                source TEXT PRIMARY KEY,
                cursor_timestamp TEXT,
                last_success TEXT,
                last_error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE extraction_checkpoints (
                source TEXT PRIMARY KEY,
                updated_at TEXT,
                last_success_at TEXT,
                error_message TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO google_sync_cursors VALUES (?, ?, ?, ?)",
            ("gmail", "2026-06-20T01:02:03Z", "2026-06-20T01:03:00Z", None),
        )
        conn.execute(
            "INSERT INTO extraction_checkpoints VALUES (?, ?, ?, ?)",
            ("drive", "2026-06-20T02:00:00Z", None, "quota"),
        )

    _mock_google(monkeypatch, authenticated=True)
    monkeypatch.setattr(sync_health, "db_path", lambda: db)

    status = sync_health.sync_status()

    assert status["status"] == "ok"
    assert status["reauth_needed"] is False
    cursor = status["tables"]["google_sync_cursors"]["items"][0]
    assert cursor["source"] == "gmail"
    assert cursor["last_success"] == "2026-06-20T01:03:00Z"
    assert cursor["last_error"] is None
    assert cursor["cursor_timestamps"] == {"cursor_timestamp": "2026-06-20T01:02:03Z"}
    checkpoint = status["tables"]["extraction_checkpoints"]["items"][0]
    assert checkpoint["source"] == "drive"
    assert checkpoint["last_error"] == "quota"


def test_sync_status_degraded_for_missing_tables(tmp_path, monkeypatch):
    db = tmp_path / "agent.db"
    sqlite3.connect(db).close()
    _mock_google(monkeypatch, authenticated=False)
    monkeypatch.setattr(sync_health, "db_path", lambda: db)

    status = sync_health.sync_status()

    assert status["status"] == "degraded"
    assert status["reauth_needed"] is True
    assert status["tables"]["google_sync_cursors"]["available"] is False
    assert status["tables"]["extraction_checkpoints"]["error"] == "table does not exist"


def test_onboarding_google_exposes_sync_status(monkeypatch):
    _mock_google(monkeypatch, authenticated=True)
    monkeypatch.setattr(
        sync_health,
        "sync_status",
        lambda: {"status": "ok", "tables": {"google_sync_cursors": {}, "extraction_checkpoints": {}}},
    )
    app = FastAPI()
    app.include_router(onboarding.router)
    client = TestClient(app)

    body = client.get("/api/onboarding/google").json()

    assert body["sync"]["status"] == "ok"
    assert set(body["sync"]["tables"]) == {"google_sync_cursors", "extraction_checkpoints"}
