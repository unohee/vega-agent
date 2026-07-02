# Created: 2026-07-02
# Purpose: WhatsApp native tools — wraps a local GoWA (go-whatsapp-web-multidevice) REST server (INT-2323)
# Dependencies: stdlib only (urllib). No OAuth — auth is the locally paired GoWA device.
# Test Status: tests/test_whatsapp_int2323.py

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

_DEFAULT_URL = "http://localhost:3777"

# Resolved GoWA device_id cache (process lifetime). v8 multi-device requires X-Device-Id
# on every request. Reset via reset_device_cache() (used by tests / after re-pairing).
_device_cache: str | None = None


def _base_url() -> str:
    return (os.getenv("VEGA_WHATSAPP_GOWA_URL") or _DEFAULT_URL).rstrip("/")


def reset_device_cache() -> None:
    """Drop the cached device_id — forces re-resolution on next call."""
    global _device_cache
    _device_cache = None


# ── HTTP (stdlib urllib) ──────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None, *, device_id: str | None = None,
         timeout: float = 30.0) -> dict:
    url = _base_url() + path
    if params:
        url += "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v not in (None, "")}
        )
    headers = {}
    if device_id:
        headers["X-Device-Id"] = device_id
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post_json(path: str, body: dict, *, device_id: str | None = None,
               timeout: float = 30.0) -> dict:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if device_id:
        headers["X-Device-Id"] = device_id
    req = urllib.request.Request(_base_url() + path, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ── Defensive parsing ─────────────────────────────────────────────────────────
# GoWA responses are inconsistent: `results` is a dict for chats (results.data)
# but a plain list for /app/devices. Handle both shapes.

def _rows(results: Any) -> list:
    if isinstance(results, dict):
        data = results.get("data")
        return data if isinstance(data, list) else []
    if isinstance(results, list):
        return results
    return []


def _devices() -> list:
    data = _get("/app/devices", timeout=30.0)
    return _rows(data.get("results"))


def _resolve_device() -> str:
    """Return the GoWA device_id (cached). Raise if the server is unreachable or unpaired."""
    global _device_cache
    if _device_cache:
        return _device_cache
    try:
        devices = _devices()
    except Exception as e:
        raise RuntimeError(
            "WhatsApp GoWA 서버에 연결할 수 없습니다 — GoWA REST 서버를 기동하세요 "
            f"(whatsapp rest --port …, {_base_url()}). 원인: {e}"
        ) from e
    if not devices:
        raise RuntimeError("WhatsApp GoWA 미연결 — QR 페어링 필요")
    dev = devices[0].get("device") or devices[0].get("jid")
    if not dev:
        raise RuntimeError("WhatsApp GoWA 미연결 — QR 페어링 필요")
    _device_cache = str(dev)
    return _device_cache


def has_paired_device(timeout: float = 3.0) -> bool:
    """Connectivity/pairing probe for the toolset gate. Never raises."""
    try:
        data = _get("/app/devices", timeout=timeout)
    except Exception:
        return False
    return bool(_rows(data.get("results")))


def _msg_text(m: dict) -> str:
    """Best-effort text extraction — GoWA message shape varies by message type."""
    for k in ("text", "message", "body", "content", "caption"):
        v = m.get(k)
        if isinstance(v, str) and v:
            return v[:4000]
        if isinstance(v, dict):
            for kk in ("text", "conversation", "caption", "body"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv:
                    return vv[:4000]
    return ""


# ── Tools ─────────────────────────────────────────────────────────────────────

def whatsapp_list_chats(limit: int = 20) -> list[dict]:
    """List recent WhatsApp chats (jid/name/last_message_time)."""
    dev = _resolve_device()
    data = _get("/chats", {"limit": int(limit)}, device_id=dev)
    out = []
    for c in _rows(data.get("results")):
        out.append({
            "jid": c.get("jid"),
            "name": c.get("name"),
            "last_message_time": c.get("last_message_time"),
            "archived": c.get("archived"),
        })
    return out


def whatsapp_read_messages(chat_jid: str, limit: int = 20) -> list[dict]:
    """Read recent messages of a specific chat. chat_jid from whatsapp_list_chats."""
    dev = _resolve_device()
    data = _get(f"/chat/{urllib.parse.quote(chat_jid, safe='@.')}/messages",
                {"limit": int(limit)}, device_id=dev)
    out = []
    for m in _rows(data.get("results")):
        out.append({
            "id": m.get("id") or m.get("message_id"),
            "sender": m.get("sender") or m.get("from") or m.get("pushname"),
            "timestamp": m.get("timestamp") or m.get("message_time"),
            "from_me": m.get("from_me", m.get("is_from_me")),
            "text": _msg_text(m),
        })
    return out


def whatsapp_send_message(phone: str, message: str) -> dict:
    """Send a WhatsApp message. phone is a number (auto-suffixed @s.whatsapp.net)
    or a full jid (individual @s.whatsapp.net / group @g.us — kept as-is)."""
    dev = _resolve_device()
    target = phone if "@" in phone else f"{phone}@s.whatsapp.net"
    data = _post_json("/send/message", {"phone": target, "message": message}, device_id=dev)
    return {
        "ok": data.get("code") == "SUCCESS",
        "code": data.get("code"),
        "phone": target,
        "results": data.get("results"),
    }


WHATSAPP_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "whatsapp_send_message",
        "description": (
            "WhatsApp 메시지를 직접 전송한다(사용자 명의, 로컬 GoWA 서버 경유). "
            "정리한 내용을 복붙 안내하지 말고 이 도구로 바로 보낸다. "
            "phone 은 국가코드 포함 번호(예: 821012345678) — @s.whatsapp.net 은 자동 부착된다. "
            "그룹은 whatsapp_list_chats 의 jid(…@g.us)를 그대로 넣는다. "
            "(GoWA 서버 미연결/미페어링 시 재연결 안내.)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "수신 번호(국가코드 포함) 또는 전체 jid(개인 …@s.whatsapp.net / 그룹 …@g.us)"},
                "message": {"type": "string", "description": "보낼 메시지 본문"},
            },
            "required": ["phone", "message"],
        },
    },
    {
        "type": "function",
        "name": "whatsapp_list_chats",
        "description": "WhatsApp 최근 채팅 목록을 조회한다. 각 항목의 jid 로 whatsapp_read_messages·whatsapp_send_message 를 호출한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "description": "최대 채팅 수"},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "whatsapp_read_messages",
        "description": "특정 WhatsApp 채팅의 최근 메시지를 읽는다. chat_jid 는 whatsapp_list_chats 결과의 jid.",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_jid": {"type": "string", "description": "채팅 jid (…@s.whatsapp.net / …@g.us)"},
                "limit": {"type": "integer", "default": 20, "description": "메시지 수"},
            },
            "required": ["chat_jid"],
        },
    },
]

WHATSAPP_TOOL_FUNCTIONS: dict[str, Any] = {
    "whatsapp_list_chats": whatsapp_list_chats,
    "whatsapp_read_messages": whatsapp_read_messages,
    "whatsapp_send_message": whatsapp_send_message,
}
