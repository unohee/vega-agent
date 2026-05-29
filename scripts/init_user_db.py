# Created: 2026-05-27
# Purpose: 신규 사용자 DB 부트스트랩 — VEGA_DATA_DIR 아래 vega.db 초기화
#   서버 첫 실행 전에 실행하거나 install.sh에서 호출.
#   모든 pipeline 모듈의 CREATE TABLE IF NOT EXISTS를 한 번에 실행.
# Dependencies: pipeline/*

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def init_db() -> Path:
    from pipeline.data_paths import db_path
    db = db_path()
    print(f"[init_db] DB 경로: {db}")

    # conversations + messages
    from pipeline.session_store import _ensure_schema
    _ensure_schema()
    print("  ✓ conversations, messages")

    # contacts
    from pipeline.contact_store import init_schema as _init_contacts, _open_db
    with _open_db() as con:
        _init_contacts(con)
    print("  ✓ contacts, contact_emails, contact_phones")

    # heartbeat 관련 테이블
    import pipeline.heartbeat as _hb
    _hb._ensure_table()            # email_digest
    _hb._ensure_suggest_cache_table()
    _hb._ensure_session_digest_table()
    _hb._ensure_briefs_table()
    print("  ✓ email_digest, suggest_cache, session_digest, daily_briefs")

    # project_state
    from pipeline.project_state import _ensure_project_state_table
    _ensure_project_state_table()
    print("  ✓ project_state")

    print(f"[init_db] 완료 — {db}")
    return db


if __name__ == "__main__":
    init_db()
