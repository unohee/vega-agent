from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


@router.get("/slack/auth")
async def slack_auth_start():
    try:
        from pipeline.auth.slack import authorize_url
        return RedirectResponse(url=authorize_url(), status_code=302)
    except Exception as e:
        return HTMLResponse(f"<h3>Slack OAuth 설정 오류</h3><pre>{str(e)}</pre>", status_code=500)


@router.get("/slack/callback")
async def slack_callback(request: Request):
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(f"<h3>Slack 인증 취소/실패</h3><pre>{error}</pre>", status_code=400)
    code = request.query_params.get("code", "")
    state = request.query_params.get("state")
    if not code:
        return HTMLResponse("<h3>Slack 인증 실패</h3><pre>code 파라미터가 없습니다.</pre>", status_code=400)
    from pipeline.auth.slack import exchange_code
    result = exchange_code(code, state=state)
    if result.get("ok"):
        team = result.get("team") or "(unknown team)"
        user = result.get("user") or "(unknown user)"
        return HTMLResponse(f"<h3>Slack 인증 완료</h3><p>team: {team}<br>user: {user}</p>")
    return HTMLResponse(
        f"<h3>Slack 인증 실패</h3><pre>{result.get('error') or 'unknown error'}</pre>",
        status_code=400,
    )


@router.get("/superthread/auth")
async def superthread_auth_start():
    try:
        from pipeline.auth.superthread import authorize_url
        return RedirectResponse(url=authorize_url(), status_code=302)
    except Exception as e:
        return HTMLResponse(f"<h3>Superthread OAuth 설정 오류</h3><pre>{str(e)}</pre>", status_code=500)


@router.get("/callback")
async def superthread_callback(request: Request):
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(f"<h3>Superthread 인증 취소/실패</h3><pre>{error}</pre>", status_code=400)
    code = request.query_params.get("code", "")
    state = request.query_params.get("state")
    if not code:
        return HTMLResponse("<h3>Superthread 인증 실패</h3><pre>code 파라미터가 없습니다.</pre>", status_code=400)
    from pipeline.auth.superthread import exchange_code
    result = exchange_code(code, state=state)
    if result.get("ok"):
        exp = result.get("expires_at") or "(만료 정보 없음)"
        return HTMLResponse(f"<h3>Superthread 인증 완료</h3><p>PAT 발급됨 · 만료: {exp}</p>")
    return HTMLResponse(
        f"<h3>Superthread 인증 실패</h3><pre>{result.get('error') or 'unknown error'}</pre>",
        status_code=400,
    )


@router.get("/google/auth")
async def google_auth_start():
    try:
        from pipeline.auth.google import authorize_url
        return RedirectResponse(url=authorize_url(), status_code=302)
    except Exception as e:
        return HTMLResponse(f"<h3>Google OAuth 설정 오류</h3><pre>{str(e)}</pre>", status_code=500)


@router.get("/google/callback")
async def google_callback(request: Request):
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(f"<h3>Google 인증 취소/실패</h3><pre>{error}</pre>", status_code=400)
    code = request.query_params.get("code", "")
    state = request.query_params.get("state")
    if not code:
        return HTMLResponse("<h3>Google 인증 실패</h3><pre>code 파라미터가 없습니다.</pre>", status_code=400)
    from pipeline.auth.google import exchange_code
    result = exchange_code(code, state=state)
    if result.get("ok"):
        email = result.get("email") or "(이메일 미확인)"
        return HTMLResponse(f"<h3>Google 인증 완료</h3><p>계정: {email}</p>")
    return HTMLResponse(
        f"<h3>Google 인증 실패</h3><pre>{result.get('error') or 'unknown error'}</pre>",
        status_code=400,
    )
