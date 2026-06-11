# Created: 2026-06-11
# Purpose: Superthread 네이티브 도구 — PAT + 동적 workspace_id 로 프로젝트/보드/카드 조회·생성 (INT-1456)
#   엔드포인트는 실 API 호출로 검증: GET projects / boards?project_id= / search?query= / cards/{id},
#   POST cards (kyte-portal st_write.py 검증 패턴). 모든 요청에 User-Agent 필수 (Cloudflare 1010 차단).
# Dependencies: pipeline.auth.superthread, stdlib
# Test Status: tests/test_tools_superthread.py

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pipeline.auth import superthread as _auth

_API_BASE = "https://api.superthread.com/v1"
_RECONNECT_MSG = "Superthread 미연결/만료 — 설정 → 워크스페이스에서 Superthread 를 연결하세요."


def _workspace_id() -> str | None:
    """Keychain 의 workspace_id. 구버전 연결(미저장)이면 users/me 로 발견해 저장."""
    ws = _auth.stored_workspace_id()
    if ws:
        return ws
    token = _auth.pat_token()
    if not token:
        return None
    ws = _auth._discover_workspace_id(token)
    if ws:
        _auth.keychain_save("workspace_id", ws)
    return ws


def _st(path: str, method: str = "GET", body: dict | None = None, params: dict | None = None) -> dict:
    token = _auth.pat_token()
    ws = _workspace_id()
    if not token or not ws:
        raise RuntimeError(_RECONNECT_MSG)
    url = f"{_API_BASE}/{ws}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    data = json.dumps(body).encode() if body else None
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": _auth._UA,
        "Accept": "application/json",
    }
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20, context=_auth._ssl_context()) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        if e.code in (401, 403):
            raise RuntimeError(f"{_RECONNECT_MSG} (HTTP {e.code})") from e
        raise RuntimeError(f"Superthread API HTTP {e.code}: {err[:300]}") from e


# ── Tools ─────────────────────────────────────────────────────────────────────

def superthread_list_projects() -> list[dict]:
    """프로젝트(스페이스) 목록."""
    data = _st("projects")
    return [
        {"id": p.get("id"), "title": p.get("title"), "board_ids": p.get("board_order") or []}
        for p in data.get("projects", [])
    ]


def superthread_list_boards(project_id: str) -> list[dict]:
    """프로젝트의 보드 목록 (각 보드의 리스트 포함 — 카드 생성 시 board_id/list_id 로 사용)."""
    data = _st("boards", params={"project_id": project_id})
    out = []
    for b in data.get("boards", []):
        out.append({
            "id": b.get("id"),
            "title": b.get("title"),
            "lists": [
                {"id": l.get("id"), "title": l.get("title"),
                 "behavior": l.get("behavior"), "total_cards": l.get("total_cards")}
                for l in b.get("lists") or []
            ],
        })
    return out


def superthread_search_cards(query: str) -> list[dict]:
    """카드 검색."""
    data = _st("search", params={"query": query})
    return [
        {"id": c.get("id"), "title": c.get("title"),
         "board": c.get("board_title"), "board_id": c.get("board_id"),
         "list": c.get("list_title"), "list_id": c.get("list_id")}
        for c in data.get("cards") or []
    ]


def superthread_get_card(card_id: str) -> dict:
    """카드 상세 (제목·본문·상태·멤버·마감일)."""
    card = _st(f"cards/{card_id}").get("card") or {}
    return {
        "id": card.get("id"),
        "title": card.get("title"),
        "content": (card.get("content") or "")[:8000],
        "board": card.get("board_title"),
        "list_id": card.get("list_id"),
        "status": card.get("status"),
        "priority": card.get("priority"),
        "due_date": card.get("due_date"),
        "members": [m.get("user_id") for m in card.get("members") or []],
        "archived": bool(card.get("archived")),
    }


def superthread_create_card(title: str, board_id: str, list_id: str,
                            content: str = "", priority: int | None = None,
                            due_date: str = "") -> dict:
    """카드 생성. board_id/list_id 는 superthread_list_boards 로 확인. due_date: YYYY-MM-DD."""
    body: dict = {"title": title, "board_id": board_id, "list_id": list_id}
    if content:
        body["content"] = content
    if priority is not None:
        body["priority"] = int(priority)
    if due_date:
        import datetime as _dt
        try:
            body["due_date"] = int(_dt.datetime.strptime(due_date, "%Y-%m-%d").timestamp()) * 1000
        except ValueError:
            raise RuntimeError(f"due_date 형식 오류 (YYYY-MM-DD): {due_date}")
    card = _st("cards", method="POST", body=body).get("card") or {}
    return {"id": card.get("id"), "title": card.get("title", title)}


SUPERTHREAD_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "superthread_list_projects",
        "description": "Superthread 프로젝트(스페이스) 목록을 조회한다.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "superthread_list_boards",
        "description": "Superthread 프로젝트의 보드와 각 보드의 리스트(컬럼)를 조회한다. 카드 생성 전 board_id/list_id 확인용.",
        "parameters": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "superthread_list_projects 의 프로젝트 ID"}},
            "required": ["project_id"],
        },
    },
    {
        "type": "function",
        "name": "superthread_search_cards",
        "description": "Superthread 카드를 검색한다.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "검색어"}},
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "superthread_get_card",
        "description": "Superthread 카드 상세(제목·본문·상태)를 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {"card_id": {"type": "string", "description": "카드 ID"}},
            "required": ["card_id"],
        },
    },
    {
        "type": "function",
        "name": "superthread_create_card",
        "description": "Superthread 카드를 생성한다. board_id/list_id 는 superthread_list_boards 로 먼저 확인.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "board_id": {"type": "string"},
                "list_id": {"type": "string"},
                "content": {"type": "string", "default": ""},
                "priority": {"type": "integer", "description": "0=none 1=low 2=medium 3=high 4=urgent"},
                "due_date": {"type": "string", "default": "", "description": "YYYY-MM-DD"},
            },
            "required": ["title", "board_id", "list_id"],
        },
    },
]

SUPERTHREAD_TOOL_FUNCTIONS: dict[str, Any] = {
    "superthread_list_projects": superthread_list_projects,
    "superthread_list_boards": superthread_list_boards,
    "superthread_search_cards": superthread_search_cards,
    "superthread_get_card": superthread_get_card,
    "superthread_create_card": superthread_create_card,
}
