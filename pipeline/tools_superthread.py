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


def _content_to_html(content: str) -> str:
    """마크다운 content 를 Superthread(TipTap) 호환 HTML 로 변환 (INT-1571).

    Superthread 카드 content 필드는 HTML 을 받는다(TipTap 에디터가 HTML 로
    직렬화·저장 — 실측: 기존 카드 content 가 모두 <p>/<ul><li>/<hr> 형태).
    LLM 이 생성한 마크다운을 그대로 POST 하면 헤딩·리스트·줄바꿈이 렌더되지
    않고 한 줄로 뭉쳐 보인다. markdown 라이브러리로 시맨틱 HTML 변환하고,
    미설치(구 배포본 등) 시 tools_google._md_to_html 로 폴백한다.
    이미 HTML 이면 이중 변환을 피한다.
    """
    if not content or not content.strip():
        return content
    stripped = content.lstrip()
    if stripped.startswith("<") and ("</" in content or "/>" in content):
        return content  # 이미 HTML — 그대로 둔다
    try:
        import markdown as _markdown
        return _markdown.markdown(
            content, extensions=["extra", "nl2br", "sane_lists"]
        )
    except Exception:
        try:
            from pipeline.tools_google import _md_to_html
            return _md_to_html(content)
        except Exception:
            return content


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


def _my_user_id() -> str | None:
    """Current user's Superthread user_id via GET /v1/users/me (no workspace prefix)."""
    tok = _auth.pat_token()
    if not tok:
        return None
    try:
        from pipeline.auth.superthread import _API_BASE as _AB, _get_json
        me = _get_json(f"{_AB}/users/me", headers={"Authorization": f"Bearer {tok}"})
        user = me.get("user") or me
        uid = user.get("id")
        return str(uid) if uid else None
    except Exception:
        return None


def superthread_my_cards(role: str = "any", include_done: bool = False) -> list[dict]:
    """Cards I am assigned to (assignee) or created (creator), across the whole workspace.

    Superthread's REST search matches text only, so "all my cards" cannot be
    pulled with a query. Instead this walks every project's boards and filters
    each embedded card by its members (assignees) and user_id (creator).
    role: 'assignee' | 'creator' | 'any'. include_done=False drops done-list cards.
    Returns due_date/priority so the caller can sort by urgency.
    """
    me = _my_user_id()
    if not me:
        raise RuntimeError(_RECONNECT_MSG)
    projects = _st("projects").get("projects", [])
    seen: set[str] = set()
    out: list[dict] = []
    for p in projects:
        for bid in p.get("board_order") or []:
            try:
                board = _st(f"boards/{bid}").get("board") or {}
            except RuntimeError:
                continue  # skip boards we cannot read rather than failing the whole sweep
            for lst in board.get("lists") or []:
                if not include_done and (lst.get("behavior") or "").lower() == "done":
                    continue
                for c in lst.get("cards") or []:
                    cid = str(c.get("id") or "")
                    if not cid or cid in seen:
                        continue
                    is_assignee = any(str(m.get("user_id")) == me for m in c.get("members") or [])
                    is_creator = str(c.get("user_id")) == me
                    if role == "assignee" and not is_assignee:
                        continue
                    if role == "creator" and not is_creator:
                        continue
                    if role == "any" and not (is_assignee or is_creator):
                        continue
                    seen.add(cid)
                    out.append({
                        "id": cid,
                        "title": c.get("title"),
                        "board": c.get("board_title") or board.get("title"),
                        "list": lst.get("title"),
                        "due_date": c.get("due_date"),
                        "priority": c.get("priority"),
                        "role": "assignee" if is_assignee else "creator",
                    })
    return out


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
        body["content"] = _content_to_html(content)
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
        "name": "superthread_my_cards",
        "description": (
            "내가 담당(assignee)하거나 생성(creator)한 Superthread 카드를 워크스페이스 "
            "전역에서 모은다. 텍스트 검색(superthread_search_cards)으로는 '내 카드 전체'를 "
            "뽑을 수 없을 때 사용. due_date/priority 를 함께 반환하므로 급한 순 정렬에 쓴다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": ["any", "assignee", "creator"],
                    "default": "any",
                    "description": "any=담당 또는 생성, assignee=담당만, creator=생성만",
                },
                "include_done": {
                    "type": "boolean",
                    "default": False,
                    "description": "true 면 완료(done) 리스트 카드도 포함",
                },
            },
            "required": [],
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
    "superthread_my_cards": superthread_my_cards,
    "superthread_get_card": superthread_get_card,
    "superthread_create_card": superthread_create_card,
}
