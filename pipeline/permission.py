# Created: 2026-05-27
# Purpose: Tool permission level classification — consent layer based (RES-223)
# Dependencies: stdlib only
# Test Status: under review

from __future__ import annotations

from enum import IntEnum


class Level(IntEnum):
    READ = 1      # read — auto-approved
    WRITE = 2     # write — auto within project dir, confirm for external
    DELETE = 3    # delete — always confirm
    ORDER = 4     # order — always confirm (financial)
    SEND = 5      # send — always confirm (email/message)


# Tool name → permission level mapping
_TOOL_LEVELS: dict[str, Level] = {
    # read
    "web_search": Level.READ,
    "web_fetch": Level.READ,
    "gmail_search": Level.READ,
    "gmail_read": Level.READ,
    "calendar_list_events": Level.READ,
    "drive_search": Level.READ,
    "drive_read": Level.READ,
    "file_read": Level.READ,
    "memory_recall": Level.READ,
    "vega_query": Level.READ,
    "widget_get": Level.READ,
    "skill_list": Level.READ,
    "linear_list_issues": Level.READ,
    "airtable_list_bases": Level.READ,
    "airtable_list_tables": Level.READ,
    "airtable_list_records": Level.READ,
    "airtable_get_records": Level.READ,
    "github_list_issues": Level.READ,
    "github_get_issue": Level.READ,
    "github_list_pulls": Level.READ,
    "github_get_pull": Level.READ,
    "github_search_code": Level.READ,
    "github_read_file": Level.READ,
    # write
    "gmail_draft": Level.WRITE,
    "gmail_modify_labels": Level.WRITE,
    "gmail_batch_modify": Level.WRITE,
    "gmail_download_attachment": Level.WRITE,
    "calendar_create_event": Level.WRITE,
    "calendar_update_event": Level.WRITE,
    "file_edit": Level.WRITE,
    "file_create": Level.WRITE,
    "skill_save": Level.WRITE,
    "skill_update": Level.WRITE,
    "memory_persona_update": Level.WRITE,
    "memory_event_add": Level.WRITE,
    "memory_entity_upsert": Level.WRITE,
    "widget_save": Level.WRITE,
    "linear_create_issue": Level.WRITE,
    "linear_update_issue": Level.WRITE,
    "airtable_create_record": Level.WRITE,
    "airtable_update_record": Level.WRITE,
    "github_create_issue": Level.WRITE,
    # delete
    "calendar_delete_event": Level.DELETE,
    "file_delete": Level.DELETE,
    "skill_delete": Level.DELETE,
    "memory_persona_delete": Level.DELETE,
    "memory_event_delete": Level.DELETE,
    # order (financial)
    "kis_order_execute": Level.ORDER,
    "kis_order_cancel": Level.ORDER,
    "kis_order_modify": Level.ORDER,
    # send
    "gmail_send": Level.SEND,
    "discord_send": Level.SEND,
    "slack_send": Level.SEND,
}

# Default policy: which levels require a consent card
_DEFAULT_REQUIRE_CONSENT: set[Level] = {Level.DELETE, Level.ORDER, Level.SEND}


def get_level(tool_name: str) -> Level:
    """Tool name → permission level. Defaults to WRITE (conservative) for unknown tools."""
    # bash_exec / python_exec family: host_exec defaults to WRITE; escalates to DELETE if ask=True
    if tool_name in ("bash_exec", "host_exec", "python_exec"):
        return Level.WRITE
    return _TOOL_LEVELS.get(tool_name, Level.WRITE)


def requires_consent(tool_name: str, policy: set[Level] | None = None) -> bool:
    """Return whether consent is required under the current policy."""
    policy = policy if policy is not None else _DEFAULT_REQUIRE_CONSENT
    return get_level(tool_name) in policy


_LEVEL_META = {
    Level.READ:   {"label": "읽기",  "color": "#6b7280", "badge": "R"},
    Level.WRITE:  {"label": "쓰기",  "color": "#3b82f6", "badge": "W"},
    Level.DELETE: {"label": "삭제",  "color": "#f97316", "badge": "D"},
    Level.ORDER:  {"label": "주문",  "color": "#ef4444", "badge": "!"},
    Level.SEND:   {"label": "전송",  "color": "#ef4444", "badge": "!"},
}


def level_meta(tool_name: str) -> dict:
    """Return badge metadata for UI rendering."""
    lvl = get_level(tool_name)
    return {"level": lvl.name, "value": int(lvl), **_LEVEL_META[lvl]}
