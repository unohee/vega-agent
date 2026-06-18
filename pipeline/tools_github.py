# Created: 2026-06-18
# Purpose: GitHub 네이티브 도구 — 이슈/PR 조회·생성, 코드 검색, 파일 읽기 (INT-1498).
#   GitHub REST API v3 직접 호출(httpx). PAT 인증. /repos/{owner}/{repo}/issues 는
#   PR 도 함께 반환하므로 pull_request 키로 필터한다(실측 확인).
# Dependencies: httpx, pipeline.auth.github
# Test Status: tests/test_tools_workspace.py
from __future__ import annotations

from typing import Any

_API = "https://api.github.com"
_RECONNECT_MSG = "GitHub 미연결 — 설정 → 워크스페이스에서 GitHub PAT 를 연결하세요."


def _require_token() -> str:
    from pipeline.auth import github as _auth
    tok = _auth.token()
    if not tok:
        raise RuntimeError(_RECONNECT_MSG)
    return tok


def _gh(method: str, path: str, params: dict | None = None, json_body: dict | None = None) -> Any:
    import httpx
    headers = {
        "Authorization": f"Bearer {_require_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vega-agent",
    }
    r = httpx.request(method, f"{_API}{path}", headers=headers,
                      params=params, json=json_body, timeout=20)
    if r.status_code in (401, 403):
        raise RuntimeError(f"{_RECONNECT_MSG} (HTTP {r.status_code}: {r.text[:150]})")
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub API HTTP {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


# ── 이슈 ────────────────────────────────────────────────────────────────────────

def github_list_issues(repo: str, state: str = "open", limit: int = 30) -> list[dict]:
    """저장소 이슈 목록. repo='owner/repo'. PR 은 제외(순수 이슈만)."""
    data = _gh("GET", f"/repos/{repo}/issues",
               params={"state": state, "per_page": min(limit, 100)})
    out = []
    for i in data:
        if "pull_request" in i:
            continue  # issues 엔드포인트는 PR 도 반환 — 제외
        out.append({
            "number": i["number"], "title": i["title"], "state": i["state"],
            "labels": [l["name"] for l in i.get("labels", [])],
            "user": (i.get("user") or {}).get("login"),
            "comments": i.get("comments", 0), "url": i.get("html_url"),
        })
    return out[:limit]


def github_get_issue(repo: str, number: int) -> dict:
    """이슈 1건 상세(본문 + 댓글). repo='owner/repo'."""
    i = _gh("GET", f"/repos/{repo}/issues/{number}")
    comments = []
    if i.get("comments", 0):
        cs = _gh("GET", f"/repos/{repo}/issues/{number}/comments", params={"per_page": 100})
        comments = [{"user": (c.get("user") or {}).get("login"),
                     "body": (c.get("body") or "")[:2000]} for c in cs]
    return {
        "number": i["number"], "title": i["title"], "state": i["state"],
        "body": (i.get("body") or "")[:8000],
        "labels": [l["name"] for l in i.get("labels", [])],
        "user": (i.get("user") or {}).get("login"), "url": i.get("html_url"),
        "comments": comments,
    }


def github_create_issue(repo: str, title: str, body: str = "", labels: list[str] | None = None) -> dict:
    """이슈 생성. repo='owner/repo'. 반드시 사용자 확인 후 실행."""
    payload: dict = {"title": title}
    if body:
        payload["body"] = body
    if labels:
        payload["labels"] = labels
    i = _gh("POST", f"/repos/{repo}/issues", json_body=payload)
    return {"number": i.get("number"), "title": i.get("title", title), "url": i.get("html_url")}


# ── PR ──────────────────────────────────────────────────────────────────────────

def github_list_pulls(repo: str, state: str = "open", limit: int = 30) -> list[dict]:
    """저장소 Pull Request 목록. repo='owner/repo'."""
    data = _gh("GET", f"/repos/{repo}/pulls",
               params={"state": state, "per_page": min(limit, 100)})
    return [{
        "number": p["number"], "title": p["title"], "state": p["state"],
        "user": (p.get("user") or {}).get("login"),
        "head": (p.get("head") or {}).get("ref"), "base": (p.get("base") or {}).get("ref"),
        "draft": p.get("draft", False), "url": p.get("html_url"),
    } for p in data][:limit]


def github_get_pull(repo: str, number: int) -> dict:
    """PR 1건 상세(본문 + 변경 파일 목록). repo='owner/repo'."""
    p = _gh("GET", f"/repos/{repo}/pulls/{number}")
    files = _gh("GET", f"/repos/{repo}/pulls/{number}/files", params={"per_page": 100})
    return {
        "number": p["number"], "title": p["title"], "state": p["state"],
        "body": (p.get("body") or "")[:8000],
        "user": (p.get("user") or {}).get("login"),
        "head": (p.get("head") or {}).get("ref"), "base": (p.get("base") or {}).get("ref"),
        "merged": p.get("merged", False), "url": p.get("html_url"),
        "files": [{"filename": f["filename"], "status": f["status"],
                   "additions": f["additions"], "deletions": f["deletions"]} for f in files],
    }


# ── 검색 / 파일 ──────────────────────────────────────────────────────────────────

def github_search_code(query: str, repo: str | None = None, limit: int = 10) -> dict:
    """GitHub 코드 검색. repo 지정 시 해당 저장소로 한정(query 에 'repo:owner/repo' 자동 부착)."""
    q = query + (f" repo:{repo}" if repo else "")
    data = _gh("GET", "/search/code", params={"q": q, "per_page": min(limit, 30)})
    return {
        "total_count": data.get("total_count", 0),
        "items": [{"repo": (it.get("repository") or {}).get("full_name"),
                   "path": it.get("path"), "url": it.get("html_url")}
                  for it in data.get("items", [])[:limit]],
    }


def github_read_file(repo: str, path: str, ref: str | None = None) -> dict:
    """저장소 파일 내용 읽기(텍스트). repo='owner/repo'. ref: 브랜치/태그/커밋(선택).

    경로가 디렉터리면 항목 목록을 반환한다.
    """
    params = {"ref": ref} if ref else None
    d = _gh("GET", f"/repos/{repo}/contents/{path}", params=params)
    if isinstance(d, list):
        return {"path": path, "type": "dir",
                "entries": [{"name": e["name"], "type": e["type"]} for e in d]}
    import base64
    content = ""
    if d.get("encoding") == "base64" and d.get("content"):
        try:
            content = base64.b64decode(d["content"]).decode("utf-8", errors="replace")
        except Exception:
            content = "[binary 파일 — 텍스트 디코드 불가]"
    return {"path": path, "size": d.get("size"),
            "content": content[:20000], "url": d.get("html_url")}


# ── 스키마 ──────────────────────────────────────────────────────────────────────

GITHUB_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "github_list_issues",
        "description": "GitHub 저장소의 이슈 목록을 조회한다. repo는 'owner/repo' 형식. PR은 제외된다.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "'owner/repo' (예: unohee/vega-agent)"},
                "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                "limit": {"type": "integer", "description": "최대 건수 (기본 30)"},
            },
            "required": ["repo"],
        },
    },
    {
        "type": "function",
        "name": "github_get_issue",
        "description": "GitHub 이슈 1건의 상세(본문·라벨·댓글)를 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "'owner/repo'"},
                "number": {"type": "integer", "description": "이슈 번호"},
            },
            "required": ["repo", "number"],
        },
    },
    {
        "type": "function",
        "name": "github_create_issue",
        "description": "GitHub 이슈를 생성한다. 반드시 사용자 확인 후 실행.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "'owner/repo'"},
                "title": {"type": "string"},
                "body": {"type": "string", "default": ""},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["repo", "title"],
        },
    },
    {
        "type": "function",
        "name": "github_list_pulls",
        "description": "GitHub 저장소의 Pull Request 목록을 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "'owner/repo'"},
                "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                "limit": {"type": "integer", "description": "최대 건수 (기본 30)"},
            },
            "required": ["repo"],
        },
    },
    {
        "type": "function",
        "name": "github_get_pull",
        "description": "GitHub PR 1건의 상세(본문 + 변경 파일 목록)를 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "'owner/repo'"},
                "number": {"type": "integer", "description": "PR 번호"},
            },
            "required": ["repo", "number"],
        },
    },
    {
        "type": "function",
        "name": "github_search_code",
        "description": "GitHub 코드 검색. repo를 지정하면 해당 저장소로 한정.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어"},
                "repo": {"type": "string", "description": "'owner/repo' (선택, 범위 한정)"},
                "limit": {"type": "integer", "description": "최대 건수 (기본 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "github_read_file",
        "description": "GitHub 저장소의 파일 내용을 읽는다(텍스트). 경로가 디렉터리면 항목 목록 반환.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "'owner/repo'"},
                "path": {"type": "string", "description": "파일 경로 (예: pipeline/tools.py)"},
                "ref": {"type": "string", "description": "브랜치/태그/커밋 (선택, 기본 default 브랜치)"},
            },
            "required": ["repo", "path"],
        },
    },
]

GITHUB_TOOL_FUNCTIONS: dict[str, Any] = {
    "github_list_issues": github_list_issues,
    "github_get_issue": github_get_issue,
    "github_create_issue": github_create_issue,
    "github_list_pulls": github_list_pulls,
    "github_get_pull": github_get_pull,
    "github_search_code": github_search_code,
    "github_read_file": github_read_file,
}
