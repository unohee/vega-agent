# Created: 2026-05-27
# Updated: 2026-05-31 — 설치 마법사 백엔드. OpenRouter 키 저장/검증, LLM 대화형 설정,
#   Google Cloud OAuth 단계, 온보딩 완료 마킹.
# Purpose: Onboarding / install-wizard API

from __future__ import annotations

import json
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


# ── 현재 상태 ────────────────────────────────────────────────────────────────

@router.get("/api/onboarding")
async def get_onboarding():
    """현재 user_profile 과 온보딩 완료 여부 반환."""
    from pipeline.user_profile import load_profile, is_onboarded
    from pipeline import keychain
    profile = load_profile()
    has_or = bool(keychain.get_secret("OPENROUTER_API")) or bool(os.environ.get("OPENROUTER_API"))
    has_google = bool(keychain.get_secret("GOOGLE_CLIENT_ID"))
    return JSONResponse({
        "onboarded": is_onboarded(),
        "profile": profile,
        "has_openrouter_key": has_or,
        "has_google": has_google,
    })


# ── OpenRouter 키 저장 + 검증 ────────────────────────────────────────────────

class KeyPayload(BaseModel):
    api_key: str = ""


@router.post("/api/onboarding/openrouter")
async def save_openrouter_key(payload: KeyPayload):
    """OpenRouter API 키를 검증하고 Keychain 에 저장.

    검증: OpenRouter /models 엔드포인트에 키로 요청해 200 이면 유효.
    """
    key = (payload.api_key or "").strip()
    if not key:
        return JSONResponse({"ok": False, "error": "API 키가 비어 있습니다."}, status_code=400)

    # 라이브 검증 — 잘못된 키를 저장하지 않는다.
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                return JSONResponse({"ok": False, "error": f"키 검증 실패 (HTTP {resp.status})"}, status_code=400)
    except urllib.error.HTTPError as e:
        return JSONResponse({"ok": False, "error": f"키가 거부되었습니다 (HTTP {e.code})"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"OpenRouter 연결 실패: {e}"}, status_code=502)

    from pipeline import keychain
    keychain.set_secret("OPENROUTER_API", key)
    # 현재 프로세스에서도 즉시 쓸 수 있도록 env 반영
    os.environ["OPENROUTER_API"] = key
    return JSONResponse({"ok": True})


# ── LLM 대화형 설정 (연결된 LLM 이 설치 과정을 진행) ──────────────────────────

class WizardChatPayload(BaseModel):
    messages: list[dict] = []  # [{"role": "user"|"assistant", "content": "..."}]


_WIZARD_SYSTEM = """당신은 VEGA 설치 마법사를 진행하는 어시스턴트다. 사용자가 막 VEGA를
설치하고 처음 실행했다. 당신의 임무는 짧고 친근한 대화로 초기 설정을 끝내는 것이다.

수집할 정보 (순서대로, 한 번에 하나씩만 질문):
1. 사용자 이름(호칭) — display_name
2. 역할/하는 일 한 줄 — role_summary
3. (선택) 소속/회사 — company
4. Google 연동(Gmail·Calendar·Drive)을 지금 연결할지 — 원하면 "GOOGLE_AUTH" 단계로 안내

규칙:
- 답변은 2~3문장 이내로 짧게. 한국어로.
- 한 번에 질문 하나만. 사용자가 답하면 다음으로 넘어간다.
- 필드를 확정했으면 그 턴 응답 맨 끝에 한 줄로 다음 JSON 을 출력한다(사용자에게는 안 보이게 ```vega 코드펜스로 감싼다):
  ```vega
  {"set": {"display_name": "홍길동"}}
  ```
  여러 필드를 한 번에 set 할 수 있다.
- Google 연동을 사용자가 원하면: ```vega {"action": "google_auth"} ``` 를 출력.
- 모든 설정이 끝났다고 판단되면: ```vega {"action": "finish"} ``` 를 출력하고 환영 인사를 한다.
- 처음 메시지(사용자 입력이 없을 때)에는 VEGA를 한 줄로 소개하고 이름부터 물어라.
"""


@router.post("/api/onboarding/chat")
async def wizard_chat(payload: WizardChatPayload):
    """설치 마법사 대화 1턴. 연결된 OpenRouter LLM 이 설정 과정을 진행한다.

    응답: {"reply": "<사용자에게 보일 텍스트>", "directives": [{"set": {...}} | {"action": "..."}]}
    LLM 출력의 ```vega ...``` 코드펜스를 파싱해 directives 로 분리하고 즉시 반영한다.
    """
    from pipeline import streaming

    # 누적 콜백 없이 한 번에 받기 위한 간단 수집기
    collected = {"text": ""}

    async def _on_token(tok: str) -> None:
        collected["text"] += tok

    msgs = payload.messages or []
    # 빈 대화면 첫 인사를 유도
    if not msgs:
        msgs = [{"role": "user", "content": "(설치 마법사 시작)"}]

    try:
        await streaming.stream_gpt(
            messages=msgs,
            system=_WIZARD_SYSTEM,
            on_token=_on_token,
            tier="cloud",      # 설치 안내는 클라우드(OpenRouter) 고정
            ce_mode=True,      # 도구 노출 최소화 — 마법사는 대화만
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"LLM 호출 실패: {e}"}, status_code=502)

    raw = collected["text"]
    reply, directives = _parse_directives(raw)

    # directive 즉시 반영
    applied = _apply_directives(directives)

    return JSONResponse({"ok": True, "reply": reply, "directives": directives, "applied": applied})


def _parse_directives(text: str) -> tuple[str, list[dict]]:
    """```vega ...``` 코드펜스를 추출해 directive 리스트로 파싱하고, 본문에서 제거한 텍스트를 반환."""
    import re
    directives: list[dict] = []
    pattern = re.compile(r"```vega\s*(.*?)```", re.DOTALL)
    for m in pattern.finditer(text):
        block = m.group(1).strip()
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                directives.append(obj)
        except Exception:
            continue
    clean = pattern.sub("", text).strip()
    return clean, directives


def _apply_directives(directives: list[dict]) -> list[str]:
    """directive 를 user_profile/액션에 반영. 반영된 항목 키 목록 반환."""
    from pipeline.user_profile import load_profile, save_profile
    applied: list[str] = []
    profile = load_profile()
    changed = False
    for d in directives:
        if "set" in d and isinstance(d["set"], dict):
            for k, v in d["set"].items():
                if k in ("display_name", "role_summary", "company") and isinstance(v, str):
                    profile[k] = v.strip()
                    applied.append(k)
                    changed = True
        action = d.get("action")
        if action == "finish":
            profile["onboarded"] = True
            applied.append("onboarded")
            changed = True
        elif action == "google_auth":
            applied.append("google_auth_requested")
    if changed:
        save_profile(profile)
    return applied


# ── Google Cloud OAuth 단계 ──────────────────────────────────────────────────

class GoogleCredsPayload(BaseModel):
    client_id: str = ""
    client_secret: str = ""


@router.post("/api/onboarding/google/creds")
async def save_google_creds(payload: GoogleCredsPayload):
    """Google Cloud OAuth 클라이언트 ID/Secret 을 Keychain 에 저장 (인증 흐름 사전 단계)."""
    cid = (payload.client_id or "").strip()
    csecret = (payload.client_secret or "").strip()
    if not cid or not csecret:
        return JSONResponse({"ok": False, "error": "Client ID/Secret 이 필요합니다."}, status_code=400)
    from pipeline import keychain
    keychain.set_secret("GOOGLE_CLIENT_ID", cid)
    keychain.set_secret("GOOGLE_CLIENT_SECRET", csecret)
    return JSONResponse({"ok": True})


@router.post("/api/onboarding/google/auth")
async def run_google_auth():
    """Google OAuth 동의 흐름 실행 — 브라우저를 열고 refresh token 을 발급받아 저장."""
    import asyncio
    try:
        from scripts.google_oauth import run_oauth_flow
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"OAuth 모듈 로드 실패: {e}"}, status_code=500)

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, run_oauth_flow)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"인증 흐름 오류: {e}"}, status_code=500)

    if not result.get("ok"):
        return JSONResponse({"ok": False, "error": result.get("error", "인증 실패")}, status_code=400)
    return JSONResponse({"ok": True})


# ── 완료 ─────────────────────────────────────────────────────────────────────

class OnboardingPayload(BaseModel):
    display_name: str = ""
    role_summary: str = ""
    company: str = ""
    email_accounts: list[dict] = []


@router.post("/api/onboarding/finish")
async def finish_onboarding(payload: OnboardingPayload):
    """프로필 저장 + DB 초기화 + 온보딩 완료 마킹."""
    from pipeline.user_profile import load_profile, save_profile
    import asyncio

    profile = load_profile()
    if payload.display_name:
        profile["display_name"] = payload.display_name.strip()
    if payload.role_summary:
        profile["role_summary"] = payload.role_summary.strip()
    if payload.company:
        profile["company"] = payload.company.strip()
    if payload.email_accounts:
        clean = []
        for acc in payload.email_accounts:
            key = (acc.get("key") or "").strip().lower()
            email = (acc.get("email") or "").strip()
            if key and email:
                clean.append({"key": key, "email": email, "label": acc.get("label") or key})
        profile["email_accounts"] = clean
    profile["onboarded"] = True
    save_profile(profile)

    # DB 부트스트랩 (멱등)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _bootstrap_db)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"DB init failed: {e}"}, status_code=500)

    # 서버 가동 중이면 account enum 갱신
    try:
        from pipeline.tools import patch_account_enum
        patch_account_enum()
    except Exception:
        pass

    return JSONResponse({"ok": True, "profile": load_profile()})


def _bootstrap_db() -> None:
    """scripts/init_user_db.py 의 init_db() 직접 호출."""
    import sys
    from pathlib import Path
    root = Path(__file__).parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.init_user_db import init_db
    init_db()
