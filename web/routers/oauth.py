from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


def _refresh_tool_availability() -> None:
    """연결 직후 워크스페이스 도구 가용성 캐시 즉시 반영 (TTL 30s 대기 없이)."""
    from pipeline.tool_registry import invalidate_check_fn_cache
    invalidate_check_fn_cache()


@router.get("/slack/auth")
async def slack_auth_start():
    try:
        from pipeline.auth.slack import authorize_url
        return RedirectResponse(url=authorize_url(), status_code=302)
    except Exception as e:
        return HTMLResponse(f"<h3>Slack OAuth 설정 오류</h3><pre>{html.escape(str(e))}</pre>", status_code=500)


@router.get("/slack/callback")
async def slack_callback(request: Request):
    # 사용자/프로바이더 제어 입력은 모두 html.escape (reflected XSS 차단 — INT-2232).
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(f"<h3>Slack 인증 취소/실패</h3><pre>{html.escape(error)}</pre>", status_code=400)
    code = request.query_params.get("code", "")
    state = request.query_params.get("state")
    if not code:
        return HTMLResponse("<h3>Slack 인증 실패</h3><pre>code 파라미터가 없습니다.</pre>", status_code=400)
    from pipeline.auth.slack import exchange_code
    result = exchange_code(code, state=state)
    if result.get("ok"):
        _refresh_tool_availability()
        team = html.escape(str(result.get("team") or "(unknown team)"))
        user = html.escape(str(result.get("user") or "(unknown user)"))
        return HTMLResponse(f"<h3>Slack 인증 완료</h3><p>team: {team}<br>user: {user}</p>")
    return HTMLResponse(
        f"<h3>Slack 인증 실패</h3><pre>{html.escape(str(result.get('error') or 'unknown error'))}</pre>",
        status_code=400,
    )


@router.get("/superthread/auth")
async def superthread_auth_start():
    try:
        from pipeline.auth.superthread import authorize_url
        return RedirectResponse(url=authorize_url(), status_code=302)
    except Exception as e:
        return HTMLResponse(f"<h3>Superthread OAuth 설정 오류</h3><pre>{html.escape(str(e))}</pre>", status_code=500)


@router.get("/callback")
async def superthread_callback(request: Request):
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(f"<h3>Superthread 인증 취소/실패</h3><pre>{html.escape(error)}</pre>", status_code=400)
    code = request.query_params.get("code", "")
    state = request.query_params.get("state")
    if not code:
        return HTMLResponse("<h3>Superthread 인증 실패</h3><pre>code 파라미터가 없습니다.</pre>", status_code=400)
    from pipeline.auth.superthread import exchange_code
    result = exchange_code(code, state=state)
    if result.get("ok"):
        _refresh_tool_availability()
        exp = html.escape(str(result.get("expires_at") or "(만료 정보 없음)"))
        return HTMLResponse(f"<h3>Superthread 인증 완료</h3><p>PAT 발급됨 · 만료: {exp}</p>")
    return HTMLResponse(
        f"<h3>Superthread 인증 실패</h3><pre>{html.escape(str(result.get('error') or 'unknown error'))}</pre>",
        status_code=400,
    )


@router.get("/google/auth")
async def google_auth_start():
    try:
        from pipeline.auth.google import authorize_url
        return RedirectResponse(url=authorize_url(), status_code=302)
    except Exception as e:
        return HTMLResponse(f"<h3>Google OAuth 설정 오류</h3><pre>{html.escape(str(e))}</pre>", status_code=500)


@router.get("/google/callback")
async def google_callback(request: Request):
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(f"<h3>Google 인증 취소/실패</h3><pre>{html.escape(error)}</pre>", status_code=400)
    code = request.query_params.get("code", "")
    state = request.query_params.get("state")
    if not code:
        return HTMLResponse("<h3>Google 인증 실패</h3><pre>code 파라미터가 없습니다.</pre>", status_code=400)
    from pipeline.auth.google import exchange_code
    result = exchange_code(code, state=state)
    if result.get("ok"):
        _refresh_tool_availability()
        email = html.escape(str(result.get("email") or "(이메일 미확인)"))
        return HTMLResponse(f"<h3>Google 인증 완료</h3><p>계정: {email}</p>")
    return HTMLResponse(
        f"<h3>Google 인증 실패</h3><pre>{html.escape(str(result.get('error') or 'unknown error'))}</pre>",
        status_code=400,
    )


@router.get("/broker/auth")
async def broker_auth_start(request: Request):
    """Start generic broker pairing. pair_url (required) and mcp_url (optional) are read from the
    query string — no company or URL source hardcoding (INT-1924)."""
    pair_url = request.query_params.get("pair_url", "")
    mcp_url = request.query_params.get("mcp_url") or None
    if not pair_url:
        return HTMLResponse(
            "<h3>Broker pairing error</h3><pre>The pair_url parameter is required.</pre>",
            status_code=400,
        )
    try:
        from pipeline.auth.broker import authorize_url
        return RedirectResponse(url=authorize_url(pair_url, mcp_url=mcp_url), status_code=302)
    except Exception as e:
        return HTMLResponse(
            f"<h3>Broker pairing configuration error</h3><pre>{html.escape(str(e))}</pre>",
            status_code=500,
        )


@router.get("/broker/callback")
async def broker_callback(request: Request):
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(
            f"<h3>Broker pairing canceled/failed</h3><pre>{html.escape(error)}</pre>",
            status_code=400,
        )
    from pipeline.auth.broker import handle_callback
    result = handle_callback(dict(request.query_params))
    if result.get("ok"):
        _refresh_tool_availability()
        label = result.get("label") or "broker"
        return HTMLResponse(
            f"<h3>Broker pairing complete</h3><p>Connection: {html.escape(label)} · tools will be available shortly.</p>"
        )
    return HTMLResponse(
        f"<h3>Broker pairing failed</h3><pre>{html.escape(result.get('error') or 'unknown error')}</pre>",
        status_code=400,
    )
