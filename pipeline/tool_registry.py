# Created: 2026-06-10
# Purpose: 워크스페이스 toolset 가용성 게이트 — 미연결 서비스의 도구를 스키마 노출·디스패치에서 차단
# Dependencies: pipeline.auth.* (check_fn 내부 lazy import)
# Test Status: tests/test_tool_registry.py
#
# hermes-agent(https://github.com/NousResearch/hermes-agent)의 도구 레지스트리 패턴 차용.
# MIT License, Copyright (c) 2025 Nous Research. 구체적으로:
#   - tools/registry.py — check_fn 가용성 프로브 + 30s TTL 캐시(_check_fn_cached),
#     invalidate_check_fn_cache(), get_definitions()의 "check_fn 실패 도구는 스키마 제외" 규칙
#   - toolsets.py — 서비스 단위 도구 그룹핑(TOOLSETS dict)
# VEGA는 기존 TOOL_SCHEMAS/TOOL_FUNCTIONS 평면 구조를 유지하므로 ToolRegistry 클래스
# 전체가 아닌 가용성 게이트 부분만 축소 이식했다.

from __future__ import annotations

import json
import threading
import time
from typing import Callable


# ── check_fn 정의 ─────────────────────────────────────────────────────────────
# 모듈 로드 비용/순환 import 회피를 위해 함수 내부에서 lazy import.

def _google_check() -> bool:
    from pipeline.auth.google import is_authenticated
    return is_authenticated()


def _linear_check() -> bool:
    # tools.py가 import 실패 시 linear_* 스키마를 이미 제외하지만,
    # 디스패치 경로까지 같은 기준으로 막기 위해 이중 게이트.
    import importlib
    importlib.import_module("pipeline.linear_client")
    return True


def _slack_check() -> bool:
    from pipeline.auth.slack import is_authenticated
    return is_authenticated()


def _superthread_check() -> bool:
    from pipeline.auth.superthread import is_authenticated
    return is_authenticated()


def _airtable_check() -> bool:
    from pipeline.auth.airtable import is_authenticated
    return is_authenticated()


def _github_check() -> bool:
    from pipeline.auth.github import is_authenticated
    return is_authenticated()


# ── toolset 정의 ──────────────────────────────────────────────────────────────
# hermes-agent toolsets.py의 TOOLSETS 패턴 — 서비스(toolset) 단위로 도구를 묶고
# check_fn 하나로 가용성을 판정한다.

WORKSPACE_TOOLSETS: dict[str, dict] = {
    "slack": {
        "description": "Slack (검색/채널/히스토리 읽기)",
        "tools": ["slack_search", "slack_list_channels", "slack_read_channel"],
        "check_fn": _slack_check,
        "connect_hint": "설정 → 워크스페이스에서 Slack 계정을 연결하라 (GET /slack/auth).",
    },
    "superthread": {
        "description": "Superthread (프로젝트/보드/카드)",
        "tools": [
            "superthread_list_projects", "superthread_list_boards",
            "superthread_search_cards", "superthread_get_card", "superthread_create_card",
        ],
        "check_fn": _superthread_check,
        "connect_hint": "설정 → 워크스페이스에서 Superthread 를 연결하라 (GET /superthread/auth).",
    },
    "google": {
        "description": "Google Workspace (Gmail/Calendar/Drive/Slides/Docs)",
        "tools": [
            "gmail_search", "gmail_read", "gmail_send", "gmail_draft",
            "gmail_modify_labels", "gmail_batch_modify",
            "gmail_list_attachments", "gmail_download_attachment", "gmail_collect_attachments",
            "calendar_list_events", "calendar_create_event",
            "calendar_update_event", "calendar_delete_event",
            "drive_search", "drive_read",
            "slides_create", "slides_append_slide",
            "docs_create", "docs_append",
        ],
        "check_fn": _google_check,
        "connect_hint": "설정 → 연결에서 Google 계정을 연결하라 (GET /google/auth).",
    },
    "linear": {
        "description": "Linear 이슈 트래킹 (네이티브 linear_* 도구)",
        "tools": [
            "linear_list_issues", "linear_get_issue", "linear_search_issues",
            "linear_create_issue", "linear_update_issue", "linear_add_comment",
        ],
        "check_fn": _linear_check,
        "connect_hint": "LINEAR_API_KEY 설정 시 MCP linear__* 서버가 자동 등록된다.",
    },
    "airtable": {
        "description": "Airtable (베이스/테이블/레코드 조회·생성·수정)",
        "tools": [
            "airtable_list_bases", "airtable_list_tables", "airtable_list_records",
            "airtable_get_records", "airtable_create_record", "airtable_update_record",
        ],
        "check_fn": _airtable_check,
        "connect_hint": "설정 → 워크스페이스에서 Airtable PAT 를 연결하라 (AIRTABLE_PERSONAL_ACCESS_TOKEN).",
    },
    "github": {
        "description": "GitHub (이슈/PR 조회·생성, 코드 검색, 파일 읽기)",
        "tools": [
            "github_list_issues", "github_get_issue", "github_create_issue",
            "github_list_pulls", "github_get_pull", "github_search_code", "github_read_file",
        ],
        "check_fn": _github_check,
        "connect_hint": "설정 → 워크스페이스에서 GitHub PAT 를 연결하라 (GITHUB_PERSONAL_ACCESS_TOKEN).",
    },
}

_TOOL_TO_TOOLSET: dict[str, str] = {
    tool: ts for ts, spec in WORKSPACE_TOOLSETS.items() for tool in spec["tools"]
}


# ── MCP 대체 (INT-2009) ────────────────────────────────────────────────────────
# 공식 MCP 서버가 로드되면, 그와 중복되는 열등한 네이티브 도구를 스키마에서 숨긴다.
# 네이티브 superthread read 도구(검색/단건/보드목록)는 카드 제목·본문 텍스트 매칭만
# 가능해 보드/리스트 전수 열거·assignee/due 필터·체크리스트/댓글 조회를 못 한다.
# 공식 superthread-mcp 의 find_tasks/task_list/checklist_list/comment_list 가 이를
# 우월하게 대체하므로, MCP 로드 시 네이티브 read 도구를 노출하지 않아야 약한 모델이
# 단순한 이름의 네이티브 검색을 골라 열거에 실패하는 일을 막는다. create_card 는
# INT-1571 의 markdown→HTML 처리가 있어 대체 대상에서 제외(유지).
_MCP_SUPERSEDED_TOOLS: dict[str, str] = {
    "superthread_list_projects": "superthread-mcp",
    "superthread_list_boards": "superthread-mcp",
    "superthread_search_cards": "superthread-mcp",
    "superthread_get_card": "superthread-mcp",
}


def _superseded_native_tools() -> set[str]:
    """대체 MCP 서버가 현재 로드된 네이티브 도구 집합. MCP 미로드 시 빈 집합 → 네이티브 fallback."""
    try:
        from pipeline.mcp_client import server_loaded
    except Exception:
        return set()
    return {t for t, srv in _MCP_SUPERSEDED_TOOLS.items() if server_loaded(srv)}


# ── check_fn TTL 캐시 ─────────────────────────────────────────────────────────
# hermes-agent tools/registry.py에서 차용. 원문 주석 인용:
#   "For a long-lived CLI or gateway process, calling them on every
#    get_definitions() is pure waste — external state changes on human
#    timescales. Cache results for ~30 s so [...] live credential file
#    changes propagate within a turn or two without requiring any
#    explicit invalidation."
# VEGA의 google check_fn도 매 호출 Keychain을 프로브하므로 동일하게 적용한다.

_CHECK_FN_TTL_SECONDS = 30.0
_check_fn_cache: dict[Callable, tuple[float, bool]] = {}
_check_fn_cache_lock = threading.Lock()


def _check_fn_cached(fn: Callable) -> bool:
    """bool(fn())을 TTL 캐시로 반환. 예외는 False로 삼킨다 (hermes 동일 semantics)."""
    now = time.monotonic()
    with _check_fn_cache_lock:
        cached = _check_fn_cache.get(fn)
        if cached is not None:
            ts, value = cached
            if now - ts < _CHECK_FN_TTL_SECONDS:
                return value
    try:
        value = bool(fn())
    except Exception:
        value = False
    with _check_fn_cache_lock:
        _check_fn_cache[fn] = (now, value)
    return value


def invalidate_check_fn_cache() -> None:
    """캐시된 check_fn 결과 전부 폐기 — 연결/해제 직후 즉시 반영용 (hermes 동일)."""
    with _check_fn_cache_lock:
        _check_fn_cache.clear()


# ── 공개 API ──────────────────────────────────────────────────────────────────

def toolset_of(tool_name: str) -> str | None:
    """도구가 속한 워크스페이스 toolset 이름. 워크스페이스 도구가 아니면 None."""
    return _TOOL_TO_TOOLSET.get(tool_name)


def is_toolset_available(toolset: str) -> bool:
    """toolset 가용성 판정. check_fn 없으면 가용, 예외는 불가용 (hermes 동일 semantics)."""
    spec = WORKSPACE_TOOLSETS.get(toolset)
    if not spec or not spec.get("check_fn"):
        return True
    return _check_fn_cached(spec["check_fn"])


def filter_available_schemas(schemas: list[dict]) -> list[dict]:
    """미연결 toolset의 도구 스키마 제외 — hermes get_definitions()의 check_fn 필터에 해당.

    워크스페이스 toolset에 속하지 않는 도구(코어/MCP)는 그대로 통과한다.
    단, 동급 MCP 서버가 로드되어 대체된 네이티브 도구는 숨긴다 (INT-2009)."""
    unavailable = {ts for ts in WORKSPACE_TOOLSETS if not is_toolset_available(ts)}
    superseded = _superseded_native_tools()
    if not unavailable and not superseded:
        return schemas
    return [
        s for s in schemas
        if _TOOL_TO_TOOLSET.get(str(s.get("name", ""))) not in unavailable
        and str(s.get("name", "")) not in superseded
    ]


def dispatch_gate(tool_name: str) -> str | None:
    """디스패치 직전 가용성 검사. 차단 시 에러 JSON 문자열, 통과 시 None.

    스키마 필터와 별개로 두는 이유: 히스토리 재생·세션 중 연결 해제 등으로
    LLM이 미노출 도구를 호출할 수 있다 — plan 모드 게이트와 같은 이중 방어."""
    ts = _TOOL_TO_TOOLSET.get(tool_name)
    if ts is None or is_toolset_available(ts):
        return None
    spec = WORKSPACE_TOOLSETS[ts]
    return json.dumps({
        "error": (
            f"'{tool_name}' 사용 불가 — {spec['description']} 워크스페이스가 "
            f"연결되어 있지 않다. {spec.get('connect_hint', '')}"
        ),
        "workspace_unavailable": ts,
    }, ensure_ascii=False)
