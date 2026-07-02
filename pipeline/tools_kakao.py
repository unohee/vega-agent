# Created: 2026-07-02
# Purpose: KakaoTalk native tool — "send to me" memo via official REST API (INT-2322).
#   POST kapi.kakao.com/v2/api/talk/memo/default/send with a text template_object.
#   Structural twin of pipeline/tools_slack.py (auth module + urlopen + schemas).
# Dependencies: pipeline.auth.kakao, stdlib
# Test Status: tests/test_kakao_int2322.py

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pipeline.auth import kakao as _auth

_SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
_RECONNECT_MSG = "카카오 토큰이 없거나 만료/무효합니다 — 설정 → 워크스페이스에서 카카오를 다시 연결하세요."
# Kakao text template caps text at 200 chars — excess is truncated, not rejected.
_TEXT_MAX = 200
# The text template schema marks "link" as required. We send an empty object when
# the caller gives no link_url; if the live API ever rejects that with HTTP 400,
# we retry once with this harmless default URL (documented Kakao developers site).
_FALLBACK_LINK = "https://developers.kakao.com"


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _post_send(template_object: dict, *, token: str) -> dict:
    """POST memo/default/send. template_object 는 form 필드의 JSON 문자열."""
    data = urllib.parse.urlencode(
        {"template_object": json.dumps(template_object, ensure_ascii=False)}
    ).encode("utf-8")
    req = urllib.request.Request(
        _SEND_URL, data=data, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as r:
        return json.loads(r.read().decode())


def kakao_send_to_me(text: str, link_url: str = "") -> dict:
    """카카오톡 '나에게 보내기'로 텍스트 메모를 전송한다 (공식 REST API).

    text 는 200자 초과분을 자른다. link_url 미지정 시 link 는 빈 객체."""
    token = _auth.access_token()
    if not token:
        raise RuntimeError(_RECONNECT_MSG)
    body = (text or "")[:_TEXT_MAX]
    link = {"web_url": link_url, "mobile_web_url": link_url} if link_url else {}
    template = {"object_type": "text", "text": body, "link": link}
    try:
        resp = _post_send(template, token=token)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        if e.code == 401:
            # Access token expired mid-flight — refresh once and retry (slack pattern).
            fresh = _auth.refresh_access_token()
            if not fresh:
                raise RuntimeError(f"{_RECONNECT_MSG} (HTTP 401)") from e
            try:
                resp = _post_send(template, token=fresh)
            except urllib.error.HTTPError as e2:
                raise RuntimeError(
                    f"카카오 메시지 전송 실패 (HTTP {e2.code}): "
                    f"{e2.read().decode('utf-8', errors='replace')[:300]}"
                ) from e2
        elif e.code == 400 and not link_url:
            # Empty link {} rejected by schema validation — fall back to a harmless
            # default URL once (see _FALLBACK_LINK note above).
            template["link"] = {"web_url": _FALLBACK_LINK, "mobile_web_url": _FALLBACK_LINK}
            try:
                resp = _post_send(template, token=token)
            except urllib.error.HTTPError as e2:
                raise RuntimeError(
                    f"카카오 메시지 전송 실패 (HTTP {e2.code}): "
                    f"{e2.read().decode('utf-8', errors='replace')[:300]}"
                ) from e2
        else:
            raise RuntimeError(f"카카오 메시지 전송 실패 (HTTP {e.code}): {detail[:300]}") from e
    # Success response: {"result_code": 0}
    if resp.get("result_code", 0) != 0:
        raise RuntimeError(f"카카오 메시지 전송 실패: {resp}")
    return {"ok": True}


KAKAO_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "kakao_send_to_me",
        "description": (
            "카카오톡 '나에게 보내기'로 텍스트 메모를 전송한다(사용자 본인 채팅방). "
            "정리한 내용을 복붙 안내하지 말고 이 도구로 바로 보낸다. "
            "text 는 200자 제한 — 초과분은 잘린다. (talk_message 동의 필요 — 미연결 시 재인증 안내.)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "보낼 메모 본문 (최대 200자)"},
                "link_url": {"type": "string", "default": "",
                             "description": "메시지에 붙일 링크 URL (생략 가능)"},
            },
            "required": ["text"],
        },
    },
]

KAKAO_TOOL_FUNCTIONS: dict[str, Any] = {
    "kakao_send_to_me": kakao_send_to_me,
}
