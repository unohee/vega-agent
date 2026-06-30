# Created: 2026-05-18
# Purpose: Agent contact DB — iCloud address book + iMessage frequency + relationship notes
# Dependencies: sqlite3 (stdlib), pipeline/tools._normalize_phone

from __future__ import annotations

import glob
import os
import sqlite3
import time
from pathlib import Path

from pipeline.data_paths import contacts_db_path as _contacts_db_path
_DB_PATH = _contacts_db_path()
_AB_SOURCE_PATTERN = os.path.expanduser(
    "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"
)
_IMESSAGE_DB = Path.home() / "Library/Messages/chat.db"


def _normalize_phone(phone: str) -> str:
    """Normalize to E.164 format (same logic as tools.py, copied here to avoid circular import)."""
    import re
    digits = re.sub(r"\D", "", phone)
    if phone.startswith("+"):
        return "+" + digits
    if re.match(r"^01[016789]", digits):
        return "+82" + digits[1:]
    if digits.startswith("02") and 9 <= len(digits) <= 10:
        return "+82" + digits[1:]
    if digits.startswith("0") and len(digits) >= 9:
        return "+82" + digits[1:]
    if digits.startswith("82") and len(digits) >= 11:
        return "+" + digits
    return digits


def _open_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
            id              INTEGER PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            nickname        TEXT,
            org             TEXT,
            job_title       TEXT,
            department      TEXT,
            note_apple      TEXT,      -- raw iCloud note text
            memo            TEXT,      -- relationship notes written directly by the agent
            imessage_count  INTEGER DEFAULT 0,
            imessage_last   TEXT,      -- ISO datetime
            synced_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS contact_phones (
            id          INTEGER PRIMARY KEY,
            contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            label       TEXT,
            phone_raw   TEXT NOT NULL,
            phone_e164  TEXT NOT NULL,
            UNIQUE(contact_id, phone_e164)
        );

        CREATE TABLE IF NOT EXISTS contact_emails (
            id          INTEGER PRIMARY KEY,
            contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            label       TEXT,
            email       TEXT NOT NULL,
            UNIQUE(contact_id, email)
        );

        CREATE INDEX IF NOT EXISTS idx_phones_e164 ON contact_phones(phone_e164);
        CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name);
    """)
    con.commit()


def _load_imessage_stats() -> dict[str, tuple[int, str]]:
    """{ e164_suffix: (message_count, last_date_iso) }"""
    if not _IMESSAGE_DB.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{_IMESSAGE_DB}?mode=ro", uri=True, timeout=3)
        rows = con.execute("""
            SELECT
                h.id,
                COUNT(*) AS cnt,
                MAX(datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime')) AS last_dt
            FROM message m
            JOIN handle h ON m.handle_id = h.rowid
            WHERE m.text IS NOT NULL
            GROUP BY h.id
        """).fetchall()
        con.close()
        stats: dict[str, tuple[int, str]] = {}
        for r in rows:
            e164 = _normalize_phone(r[0])
            suffix = e164[-9:] if len(e164) >= 9 else e164
            cnt, last_dt = r[1], r[2]
            if suffix in stats:
                old_cnt, old_dt = stats[suffix]
                stats[suffix] = (old_cnt + cnt, max(old_dt, last_dt))
            else:
                stats[suffix] = (cnt, last_dt)
        return stats
    except Exception:
        return {}


def sync_from_icloud(con: sqlite3.Connection) -> int:
    """Sync iCloud AddressBook sqlite → contacts DB. Returns the number of upserted contacts."""
    ab_dbs = glob.glob(_AB_SOURCE_PATTERN)
    if not ab_dbs:
        return 0

    imsg_stats = _load_imessage_stats()
    synced_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    upserted = 0

    for ab_path in ab_dbs:
        try:
            ab = sqlite3.connect(f"file:{ab_path}?mode=ro", uri=True, timeout=3)
            ab.row_factory = sqlite3.Row

            # Load basic contact info
            people = ab.execute("""
                SELECT
                    r.Z_PK,
                    TRIM(COALESCE(r.ZFIRSTNAME,'') || ' ' || COALESCE(r.ZLASTNAME,'')) AS full_name,
                    r.ZORGANIZATION,
                    r.ZNICKNAME,
                    r.ZJOBTITLE,
                    r.ZDEPARTMENT,
                    r.ZNOTE
                FROM ZABCDRECORD r
                WHERE r.ZFIRSTNAME IS NOT NULL
                   OR r.ZLASTNAME   IS NOT NULL
                   OR r.ZORGANIZATION IS NOT NULL
            """).fetchall()

            # Bulk load phone numbers
            phones_raw = ab.execute(
                "SELECT ZOWNER, ZLABEL, ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZFULLNUMBER IS NOT NULL"
            ).fetchall()
            phones_by_owner: dict[int, list[tuple[str, str]]] = {}
            for ph in phones_raw:
                phones_by_owner.setdefault(ph["ZOWNER"], []).append(
                    (ph["ZLABEL"] or "", ph["ZFULLNUMBER"])
                )

            # Bulk load email addresses
            emails_raw = ab.execute(
                "SELECT ZOWNER, ZLABEL, ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZADDRESS IS NOT NULL"
            ).fetchall()
            emails_by_owner: dict[int, list[tuple[str, str]]] = {}
            for em in emails_raw:
                emails_by_owner.setdefault(em["ZOWNER"], []).append(
                    (em["ZLABEL"] or "", em["ZADDRESS"])
                )

            ab.close()

            for p in people:
                try:
                    name = p["full_name"].strip() or (p["ZORGANIZATION"] or "").strip()
                    if not name:
                        continue

                    org        = p["ZORGANIZATION"] or ""
                    nickname   = p["ZNICKNAME"] or ""
                    job_title  = p["ZJOBTITLE"] or ""
                    department = p["ZDEPARTMENT"] or ""
                    note_apple = (p["ZNOTE"] or "")[:500]

                    # Aggregate iMessage frequency
                    imsg_count = 0
                    imsg_last  = ""
                    for _label, raw_ph in phones_by_owner.get(p["Z_PK"], []):
                        e164   = _normalize_phone(raw_ph)
                        suffix = e164[-9:] if len(e164) >= 9 else e164
                        if suffix in imsg_stats:
                            _cnt, _last_dt = imsg_stats[suffix]
                            imsg_count += _cnt
                            if _last_dt > imsg_last:
                                imsg_last = _last_dt

                    con.execute("""
                        INSERT INTO contacts (name, nickname, org, job_title, department, note_apple,
                                             imessage_count, imessage_last, synced_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(name) DO UPDATE SET
                            nickname       = excluded.nickname,
                            org            = COALESCE(NULLIF(excluded.org,''), org),
                            job_title      = COALESCE(NULLIF(excluded.job_title,''), job_title),
                            department     = COALESCE(NULLIF(excluded.department,''), department),
                            note_apple     = excluded.note_apple,
                            imessage_count = excluded.imessage_count,
                            imessage_last  = excluded.imessage_last,
                            synced_at      = excluded.synced_at
                    """, (name, nickname, org, job_title, department, note_apple,
                          imsg_count, imsg_last, synced_at))

                    row = con.execute("SELECT id FROM contacts WHERE name=?", (name,)).fetchone()
                    if not row:
                        continue
                    contact_id = row["id"]

                    for label, raw_ph in phones_by_owner.get(p["Z_PK"], []):
                        e164 = _normalize_phone(raw_ph)
                        con.execute("""
                            INSERT OR IGNORE INTO contact_phones (contact_id, label, phone_raw, phone_e164)
                            VALUES (?,?,?,?)
                        """, (contact_id, label, raw_ph, e164))

                    for label, addr in emails_by_owner.get(p["Z_PK"], []):
                        con.execute("""
                            INSERT OR IGNORE INTO contact_emails (contact_id, label, email)
                            VALUES (?,?,?)
                        """, (contact_id, label, addr.lower()))

                    upserted += 1

                except Exception:
                    continue

        except Exception:
            continue

    con.commit()
    return upserted


# ── Agent query API ────────────────────────────────────────────────────────────

def search_contacts(query: str, limit: int = 10) -> list[dict]:
    """Keyword search across name, nickname, org, and memo fields."""
    con = _open_db()
    rows = con.execute("""
        SELECT c.id, c.name, c.nickname, c.org, c.job_title, c.memo,
               c.imessage_count, c.imessage_last,
               GROUP_CONCAT(DISTINCT p.phone_e164) AS phones,
               GROUP_CONCAT(DISTINCT e.email)      AS emails
        FROM contacts c
        LEFT JOIN contact_phones p ON p.contact_id = c.id
        LEFT JOIN contact_emails e ON e.contact_id = c.id
        WHERE c.name LIKE ? OR c.nickname LIKE ? OR c.org LIKE ?
           OR c.memo LIKE ? OR c.note_apple LIKE ?
        GROUP BY c.id
        ORDER BY c.imessage_count DESC, c.name
        LIMIT ?
    """, (f"%{query}%",) * 5 + (limit,)).fetchall()
    con.close()
    return [_row_to_dict(r) for r in rows]


def get_contact_by_phone(phone: str) -> dict | None:
    """Look up a contact by phone number (any format)."""
    e164 = _normalize_phone(phone)
    suffix = e164[-9:] if len(e164) >= 9 else e164
    con = _open_db()
    row = con.execute("""
        SELECT c.id, c.name, c.nickname, c.org, c.job_title, c.memo,
               c.imessage_count, c.imessage_last,
               GROUP_CONCAT(DISTINCT p.phone_e164) AS phones,
               GROUP_CONCAT(DISTINCT e.email)      AS emails
        FROM contacts c
        JOIN contact_phones p ON p.contact_id = c.id
        LEFT JOIN contact_emails e ON e.contact_id = c.id
        WHERE p.phone_e164 LIKE ?
        GROUP BY c.id
        LIMIT 1
    """, (f"%{suffix}",)).fetchone()
    con.close()
    return _row_to_dict(row) if row else None


def update_memo(name: str, memo: str) -> bool:
    """Update a contact's relationship memo. Returns True on success."""
    name = (name or "").strip()
    if not name:
        # 빈 이름은 fallback LIKE '%' 가 돼 모든 연락처 memo 를 덮어쓴다 — 거부 (INT-2236)
        return False
    con = _open_db()
    # 정확 일치 우선 — LIKE '%name%'는 동명 부분일치 전체 덮어씀(INT-1523)
    cur = con.execute("UPDATE contacts SET memo=? WHERE name=?", (memo, name))
    if cur.rowcount == 0:
        # 정확 일치 없으면 접두사 LIKE — 단 wildcard('%'/'_') 이스케이프 + 단일 행만 (INT-2236).
        # 미이스케이프 시 입력의 와일드카드/공통 prefix 가 여러 연락처를 한꺼번에 덮어쓴다.
        like = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        cur = con.execute(
            "UPDATE contacts SET memo=? WHERE id = ("
            "SELECT id FROM contacts WHERE name LIKE ? ESCAPE '\\' ORDER BY name LIMIT 1)",
            (memo, like),
        )
    con.commit()
    con.close()
    return cur.rowcount > 0


def get_frequent_contacts(limit: int = 20) -> list[dict]:
    """Return top contacts ranked by iMessage frequency."""
    con = _open_db()
    rows = con.execute("""
        SELECT c.id, c.name, c.nickname, c.org, c.job_title, c.memo,
               c.imessage_count, c.imessage_last,
               GROUP_CONCAT(DISTINCT p.phone_e164) AS phones,
               GROUP_CONCAT(DISTINCT e.email)      AS emails
        FROM contacts c
        LEFT JOIN contact_phones p ON p.contact_id = c.id
        LEFT JOIN contact_emails e ON e.contact_id = c.id
        WHERE c.imessage_count > 0
        GROUP BY c.id
        ORDER BY c.imessage_count DESC
        LIMIT ?
    """, (limit,)).fetchall()
    con.close()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["phones"] = [p for p in (d.get("phones") or "").split(",") if p]
    d["emails"] = [e for e in (d.get("emails") or "").split(",") if e]
    return d


# ── Initialization helpers ─────────────────────────────────────────────────────

def startup_sync() -> dict:
    """Run once at server startup. Initializes the DB and resyncs from iCloud."""
    con = _open_db()
    init_schema(con)
    count = sync_from_icloud(con)
    total = con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    con.close()
    return {"synced": count, "total": total}
