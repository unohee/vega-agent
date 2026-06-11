# Created: 2026-06-11
# Purpose: Slack 네이티브 도구 — user token(xoxp)으로 검색·채널·히스토리 읽기 (INT-1456)
# Dependencies: pipeline.auth.slack, stdlib
# Test Status: tests/test_tools_slack.py

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from typing import Any

from pipeline.auth import slack as _auth

_API_BASE = "https://slack.com/api"
_RECONNECT_MSG = "Slack 토큰이 만료/무효합니다 — 설정 → 워크스페이스에서 Slack을 다시 연결하세요."

# user_id → display name 캐시 (프로세스 수명)
_user_names: dict[str, str] = {}


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _call(method: str, params: dict | None = None, *, token: str) -> dict:
    url = f"{_API_BASE}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as r:
        return json.loads(r.read().decode())


def _slack_api(method: str, params: dict | None = None) -> dict:
    """Slack Web API 호출. token_expired 면 rotation 갱신 1회 재시도."""
    token = _auth.user_token()
    if not token:
        raise RuntimeError(_RECONNECT_MSG)
    data = _call(method, params, token=token)
    if not data.get("ok") and data.get("error") in ("token_expired", "invalid_auth", "token_revoked"):
        fresh = _auth.refresh_user_token()
        if not fresh:
            raise RuntimeError(f"{_RECONNECT_MSG} (Slack: {data.get('error')})")
        data = _call(method, params, token=fresh)
    if not data.get("ok"):
        raise RuntimeError(f"Slack API {method} 오류: {data.get('error')}")
    return data


def _name_of(user_id: str) -> str:
    if not user_id:
        return "?"
    if user_id not in _user_names:
        try:
            u = _slack_api("users.info", {"user": user_id}).get("user") or {}
            _user_names[user_id] = (
                (u.get("profile") or {}).get("display_name") or u.get("real_name") or u.get("name") or user_id
            )
        except Exception:
            _user_names[user_id] = user_id
    return _user_names[user_id]


# ── Tools ─────────────────────────────────────────────────────────────────────

def slack_list_channels(types: str = "public_channel,private_channel", limit: int = 100) -> list[dict]:
    """채널/DM 목록. types: public_channel, private_channel, im, mpim (쉼표 구분)."""
    data = _slack_api("conversations.list", {
        "types": types, "limit": min(int(limit), 200), "exclude_archived": "true",
    })
    out = []
    for c in data.get("channels", []):
        out.append({
            "id": c.get("id"),
            "name": c.get("name") or (_name_of(c.get("user", "")) if c.get("is_im") else ""),
            "is_private": bool(c.get("is_private")),
            "is_im": bool(c.get("is_im")),
            "num_members": c.get("num_members"),
        })
    return out


def slack_read_channel(channel: str, limit: int = 20, oldest: str = "") -> list[dict]:
    """채널/DM 메시지 히스토리 (최신순). channel 은 ID(C…/D…) 또는 #이름."""
    if channel.startswith("#"):
        wanted = channel[1:]
        match = next((c for c in slack_list_channels() if c["name"] == wanted), None)
        if not match:
            raise RuntimeError(f"채널을 찾을 수 없음: {channel}")
        channel = match["id"]
    data = _slack_api("conversations.history", {
        "channel": channel, "limit": min(int(limit), 100), "oldest": oldest,
    })
    out = []
    for m in data.get("messages", []):
        out.append({
            "ts": m.get("ts"),
            "user": _name_of(m.get("user", "")) if m.get("user") else (m.get("username") or "bot"),
            "text": (m.get("text") or "")[:2000],
            "thread_replies": m.get("reply_count", 0),
        })
    return out


def slack_search(query: str, count: int = 10) -> list[dict]:
    """워크스페이스 메시지 검색 (search.messages). Slack 검색 연산자(in:#채널, from:@유저) 사용 가능."""
    data = _slack_api("search.messages", {"query": query, "count": min(int(count), 50)})
    out = []
    for m in (data.get("messages") or {}).get("matches", []):
        out.append({
            "channel": (m.get("channel") or {}).get("name"),
            "user": m.get("username") or _name_of(m.get("user", "")),
            "ts": m.get("ts"),
            "text": (m.get("text") or "")[:2000],
            "permalink": m.get("permalink"),
        })
    return out


SLACK_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "slack_search",
        "description": "Slack 워크스페이스 메시지를 검색한다. in:#채널, from:@유저 같은 Slack 검색 연산자 사용 가능.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어 (Slack 검색 문법)"},
                "count": {"type": "integer", "default": 10, "description": "최대 결과 수 (≤50)"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "slack_list_channels",
        "description": "Slack 채널/DM 목록을 조회한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "types": {"type": "string", "default": "public_channel,private_channel",
                          "description": "쉼표 구분: public_channel, private_channel, im, mpim"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "slack_read_channel",
        "description": "Slack 채널/DM의 최근 메시지를 읽는다. channel 은 ID 또는 #이름.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "채널 ID(C…/D…) 또는 #채널이름"},
                "limit": {"type": "integer", "default": 20, "description": "메시지 수 (≤100)"},
                "oldest": {"type": "string", "default": "", "description": "이 ts 이후만 (생략 시 최신)"},
            },
            "required": ["channel"],
        },
    },
]

SLACK_TOOL_FUNCTIONS: dict[str, Any] = {
    "slack_search": slack_search,
    "slack_list_channels": slack_list_channels,
    "slack_read_channel": slack_read_channel,
}
