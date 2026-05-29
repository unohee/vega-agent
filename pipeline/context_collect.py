# Created: 2026-05-19
# Purpose: Raw data collection for calendar/Things/Linear/mail — shared by dashboard context and daily brief
# Dependencies: pipeline/tools.py, pipeline/linear_client.py, pipeline/heartbeat.py
# Test Status: in review

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
from pipeline.data_paths import db_path as _db_path
DB_PATH = _db_path()


def collect_calendar(days: int = 7) -> dict[str, list[str]]:
    """Group upcoming N-day events by date. {YYYY-MM-DD: ["HH:MM title", ...]}."""
    events_by_day: dict[str, list[str]] = {}
    try:
        from pipeline.tools import calendar_list_events
        raw = calendar_list_events(days_from_today=days, max_results=30)
        for e in raw:
            start = e.get("start", "")
            try:
                if "T" in start:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    if dt.tzinfo:
                        dt = dt.astimezone(KST)
                    date_k = dt.strftime("%Y-%m-%d")
                    time_k = dt.strftime("%H:%M")
                else:
                    date_k = start[:10]
                    time_k = "종일"
            except Exception:
                continue
            events_by_day.setdefault(date_k, []).append(
                f"{time_k} {e.get('summary', '')}"
            )
    except Exception:
        pass
    return events_by_day


def collect_things_today() -> list[dict]:
    """Today's incomplete Things tasks.

    [Deferred] Things integration is excluded from the harness due to an infinite
    hang caused by TCC permission checks when accessing Group Containers paths
    in a LaunchAgent (daemon) context. Returns an empty list until an alternative
    integration method is found.
    """
    return []


def collect_linear_in_progress(limit: int = 8, assignee: str | None = None) -> list[dict]:
    """Linear In Progress issues. Supports assignee filter."""
    try:
        from pipeline.linear_client import list_issues
        issues = list_issues(states=["In Progress"], limit=limit, assignee=assignee) or []
        # Sort by nearest due date (issues with due_date first)
        def _sort_key(i: dict):
            d = i.get("due_date") or ""
            return (0 if d else 1, d)
        return sorted(issues, key=_sort_key)
    except Exception:
        return []


def collect_priority_mail_since(hours: int = 24) -> list[dict]:
    """High/medium priority mail scanned within the last N hours. Queries email_digest directly."""
    cutoff = (datetime.now(KST) - timedelta(hours=hours)).isoformat()
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT subject, sender, date_str, snippet, reason, action,
                       scanned_at, priority
                FROM email_digest
                WHERE priority IN ('high','medium') AND scanned_at >= ?
                ORDER BY CASE priority WHEN 'high' THEN 0 ELSE 1 END,
                         scanned_at DESC
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
