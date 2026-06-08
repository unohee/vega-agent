# Created: 2026-05-27
# Updated: 2026-05-31 — 멀티 프로바이더 설치 마법사. 프로바이더 목록→선택→해당 인증.
#   지원: ChatGPT(PKCE OAuth), Anthropic(API 키), OpenAI(API 키),
#         OpenRouter(API 키), 로컬·온프레미스(OpenAI 호환 URL). + Google Cloud OAuth 단계.
# Purpose: Onboarding / install-wizard API

from __future__ import annotations

import json
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


def _ssl_context():
    """PyInstaller 번들에서 시스템 CA를 못 찾는 경우를 위해 certifi CA를 명시한다."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ── 프로바이더 카탈로그 — 마법사가 목록으로 보여줄 선택지 ──────────────────────
# auth: "pkce"(브라우저 OAuth) | "key"(API 키) | "local"(URL만)
PROVIDER_CATALOG = [
    {
        "id": "anthropic", "label": "Anthropic (Claude)", "auth": "key",
        "key_env": "ANTHROPIC_API_KEY", "key_hint": "sk-ant-...",
        "verify_url": "https://api.anthropic.com/v1/models",
        "verify_header": "x-api-key", "verify_extra": {"anthropic-version": "2023-06-01"},
        "default_model": "claude-opus-4-8",
        "desc": "Claude 직접 API. 콘솔에서 발급한 키.",
    },
    {
        "id": "openai", "label": "OpenAI API", "auth": "key",
        "key_env": "OPENAI_API_KEY", "key_hint": "sk-...",
        "verify_url": "https://api.openai.com/v1/models",
        "verify_header": "bearer", "default_model": "gpt-5.5",
        "desc": "OpenAI 직접 API. platform.openai.com 발급 키.",
    },
    {
        "id": "openrouter", "label": "OpenRouter", "auth": "key",
        "key_env": "OPENROUTER_API", "key_hint": "sk-or-v1-...",
        "verify_url": "https://openrouter.ai/api/v1/key",
        "verify_header": "bearer", "default_model": "deepseek/deepseek-v4-flash",
        "desc": "한 키로 Claude·GPT·Gemini·DeepSeek 등 모두 접근.",
    },
    {
        "id": "chatgpt", "label": "ChatGPT (Codex, 로그인)", "auth": "pkce",
        "desc": "ChatGPT 계정으로 브라우저 로그인 (PKCE OAuth). 키 불필요.",
    },
    {
        "id": "local", "label": "로컬 / 온프레미스 서버", "auth": "local",
        "default_url": "http://localhost:1234/v1", "default_model": "",
        "desc": "LM Studio·Ollama·사내 OpenAI 호환 서버. URL만 입력.",
    },
]


def _catalog_entry(pid: str) -> dict | None:
    return next((p for p in PROVIDER_CATALOG if p["id"] == pid), None)


# ── 현재 상태 ────────────────────────────────────────────────────────────────

def _provider_configured(entry: dict) -> bool:
    """프로바이더가 사용 가능하게 설정돼 있는지(키/URL/OAuth 보유). 키 값은 보지 않는다."""
    from pipeline import keychain
    auth = entry.get("auth")
    if auth == "key":
        key_env = entry.get("key_env", "")
        if not key_env:
            return False
        # keychain.get: Keychain → .env → 환경변수 순으로 탐색
        return bool(keychain.get(key_env))
    if auth == "pkce":  # ChatGPT — OAuth 프로필 파일 존재 여부
        try:
            from pipeline.auth.chatgpt import _load_profile
            return _load_profile() is not None
        except Exception:
            return False
    if auth == "local":  # llm_providers.json 에 local base_url 이 등록됐는지
        try:
            from pipeline.llm_gateway import _provider_by_name
            prov = _provider_by_name("local")
            return bool(prov and prov.get("base_url"))
        except Exception:
            return False
    return False


@router.get("/api/onboarding")
async def get_onboarding():
    """현재 user_profile, 온보딩 여부, 프로바이더 카탈로그, 활성 프로바이더 반환.
    각 프로바이더에는 configured(키/URL/OAuth 보유 여부) 플래그가 붙는다 — 키 값은 노출 안 함."""
    from pipeline.user_profile import load_profile, is_onboarded
    from pipeline import keychain
    profile = load_profile()
    has_google = bool(keychain.get_secret("GOOGLE_CLIENT_ID"))
    try:
        from pipeline.llm_gateway import get_active_name
        active = get_active_name()
    except Exception:
        active = ""
    return JSONResponse({
        "onboarded": is_onboarded(),
        "profile": profile,
        "providers": [
            {
                **{k: v for k, v in p.items() if not k.startswith("verify")},
                "configured": _provider_configured(p),
            }
            for p in PROVIDER_CATALOG
        ],
        "active_provider": active,
        "has_google": has_google,
    })


# ── 키 출처 진단 ──────────────────────────────────────────────────────────────

@router.get("/api/onboarding/key-source")
async def key_source():
    """각 프로바이더 키가 Keychain/.env/환경변수 중 어디서 오는지 진단(값은 마스킹).
    배포본(.app)에서 '키가 왜 안 잡히나'를 추적하기 위한 용도."""
    from pipeline import keychain
    out = {}
    for entry in PROVIDER_CATALOG:
        key_env = entry.get("key_env")
        if not key_env:
            continue
        out[entry["id"]] = {"key_env": key_env, **keychain.describe_source(key_env)}
    # Google OAuth 클라이언트도 함께 진단
    out["google"] = {"key_env": "GOOGLE_CLIENT_ID", **keychain.describe_source("GOOGLE_CLIENT_ID")}
    # 탐색 중인 .env 경로(존재 여부 포함)
    import os as _os
    env_paths = [
        {"path": str(p), "exists": _os.path.exists(p)}
        for p in keychain._env_file_paths()
    ]
    return JSONResponse({"keys": out, "env_paths": env_paths})


# ── 프로바이더 설정 (키/URL/PKCE) ─────────────────────────────────────────────

class ProviderPayload(BaseModel):
    provider: str = ""
    api_key: str = ""
    base_url: str = ""       # local 전용
    model: str = ""          # 선택 — 기본 모델 오버라이드
    make_active: bool = True


def _verify_key(entry: dict, key: str) -> tuple[bool, str]:
    """프로바이더 /models 엔드포인트에 키로 요청해 유효성 확인."""
    import urllib.request
    import urllib.error
    url = entry.get("verify_url", "")
    if not url:
        return True, ""
    headers = dict(entry.get("verify_extra") or {})
    if entry.get("verify_header") == "x-api-key":
        headers["x-api-key"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as resp:
            return (resp.status == 200), ("" if resp.status == 200 else f"HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        return False, f"키가 거부되었습니다 (HTTP {e.code})"
    except Exception as e:
        return False, f"연결 실패: {e}"


@router.post("/api/onboarding/provider")
async def configure_provider(payload: ProviderPayload):
    """선택한 프로바이더를 설정한다.

    - key 타입: 키 라이브 검증 → Keychain 저장 → llm_providers.json upsert
    - local 타입: base_url 등록 (인증 없음)
    - pkce 타입: 여기서 처리하지 않음 → /api/onboarding/pkce 사용
    반환: {"ok", "active": <provider id>} 또는 에러.
    """
    entry = _catalog_entry(payload.provider)
    if not entry:
        return JSONResponse({"ok": False, "error": f"알 수 없는 프로바이더: {payload.provider}"}, status_code=400)

    from pipeline import keychain
    from pipeline.llm_gateway import upsert_provider, set_active

    if entry["auth"] == "key":
        key = (payload.api_key or "").strip()
        if not key:
            return JSONResponse({"ok": False, "error": "API 키가 비어 있습니다."}, status_code=400)
        ok, err = _verify_key(entry, key)
        if not ok:
            return JSONResponse({"ok": False, "error": err or "키 검증 실패"}, status_code=400)
        key_env = entry["key_env"]
        keychain.set_secret(key_env, key)
        os.environ[key_env] = key  # 현재 프로세스 즉시 반영
        prov_entry = _provider_json_for(entry, payload.model)
        upsert_provider(entry["id"], prov_entry)
        if payload.make_active:
            set_active(entry["id"])
        return JSONResponse({"ok": True, "active": entry["id"]})

    if entry["auth"] == "local":
        base = (payload.base_url or entry.get("default_url") or "").strip().rstrip("/")
        if not base:
            return JSONResponse({"ok": False, "error": "서버 URL이 필요합니다."}, status_code=400)
        # 라이브 확인 (GET /models) — 실패해도 등록은 허용(아직 안 띄웠을 수 있음)
        reachable = _local_reachable(base)
        prov_entry = {
            "label": "로컬/온프레미스",
            "kind": "chat_completions",
            "auth_type": "none",
            "base_url": base,
            "default_model": (payload.model or "").strip(),
        }
        upsert_provider("local", prov_entry)
        if payload.make_active:
            set_active("local")
        return JSONResponse({"ok": True, "active": "local", "reachable": reachable})

    return JSONResponse({"ok": False, "error": "이 프로바이더는 PKCE 로그인을 사용하세요."}, status_code=400)


def _provider_json_for(entry: dict, model_override: str) -> dict:
    """카탈로그 항목 → llm_providers.json provider entry."""
    pid = entry["id"]
    model = (model_override or "").strip() or entry.get("default_model", "")
    if pid == "anthropic":
        return {"label": "Anthropic (Claude)", "kind": "anthropic",
                "auth_type": "anthropic_key", "api_key_env": entry["key_env"],
                "base_url": "https://api.anthropic.com/v1", "default_model": model}
    if pid == "openai":
        return {"label": "OpenAI API", "kind": "chat_completions",
                "auth_type": "bearer", "api_key_env": entry["key_env"],
                "base_url": "https://api.openai.com/v1", "default_model": model}
    if pid == "openrouter":
        return {"label": "OpenRouter", "kind": "chat_completions",
                "auth_type": "bearer", "api_key_env": entry["key_env"],
                "base_url": "https://openrouter.ai/api/v1", "default_model": model,
                "extra_headers": {"HTTP-Referer": "https://github.com/unohee/VEGA", "X-Title": "VEGA"}}
    # fallback
    return {"label": entry["label"], "kind": "chat_completions",
            "auth_type": "bearer", "api_key_env": entry["key_env"],
            "base_url": "", "default_model": model}


def _local_reachable(base: str) -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(base.rstrip("/") + "/models", method="GET")
        with urllib.request.urlopen(req, timeout=3, context=_ssl_context()):
            return True
    except Exception:
        return False


# ── ChatGPT PKCE 로그인 ───────────────────────────────────────────────────────

@router.post("/api/onboarding/pkce")
async def pkce_login(payload: ProviderPayload):
    """ChatGPT PKCE OAuth — 브라우저를 열어 로그인하고 토큰을 저장한다."""
    if payload.provider != "chatgpt":
        return JSONResponse({"ok": False, "error": "PKCE는 chatgpt만 지원합니다."}, status_code=400)
    import asyncio
    try:
        from pipeline.auth.chatgpt import login
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"OAuth 모듈 로드 실패: {e}"}, status_code=500)

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, login)  # 브라우저 동의 → 토큰 저장
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"로그인 실패: {e}"}, status_code=400)

    from pipeline.llm_gateway import set_active
    if payload.make_active:
        set_active("chatgpt")
    return JSONResponse({"ok": True, "active": "chatgpt"})


# ── LLM 대화형 설정 (연결된 LLM 이 설치 과정을 진행) ──────────────────────────

class WizardChatPayload(BaseModel):
    messages: list[dict] = []  # [{"role": "user"|"assistant", "content": "..."}]


_WIZARD_SYSTEM = """당신은 VEGA 설치 마법사를 진행하는 어시스턴트다. 사용자가 막 VEGA를
설치하고 LLM 프로바이더를 연결한 직후다. 당신의 임무는 짧고 친근한 대화로 초기 설정을 끝내는 것이다.

수집할 정보 (순서대로, 한 번에 하나씩만 질문):
1. 사용자 이름(호칭) — display_name
2. 역할/하는 일 한 줄 — role_summary
3. (선택) 소속/회사 — company
4. Google 연동(Gmail·Calendar·Drive)을 지금 연결할지 — 원하면 "GOOGLE_AUTH" 단계로 안내

규칙:
- 답변은 2~3문장 이내로 짧게. 한국어로.
- 한 번에 질문 하나만. 사용자가 답하면 다음으로 넘어간다.
- 필드를 확정했으면 그 턴 응답 맨 끝에 한 줄로 다음 JSON 을 ```vega 코드펜스로 감싼다(사용자에겐 안 보임):
  ```vega
  {"set": {"display_name": "홍길동"}}
  ```
- Google 연동을 사용자가 원하면: ```vega {"action": "google_auth"} ``` 를 출력.
  (Google 단계 화면에서 Slack 연동까지 이어서 안내되므로, 너는 google_auth 까지만 트리거하면 된다.)
- 사용자가 Google 을 원치 않고 곧장 끝내려 하면: ```vega {"action": "finish"} ``` 출력 후 환영 인사.
- 처음 메시지(사용자 입력 없음)에는 VEGA를 한 줄로 소개하고 이름부터 물어라.
"""


@router.post("/api/onboarding/chat")
async def wizard_chat(payload: WizardChatPayload):
    """설치 마법사 대화 1턴. 연결된 LLM(활성 프로바이더)이 설정 과정을 진행한다."""
    from pipeline import streaming

    collected = {"text": ""}

    async def _on_token(tok: str) -> None:
        collected["text"] += tok

    msgs = payload.messages or []
    if not msgs:
        msgs = [{"role": "user", "content": "(설치 마법사 시작)"}]

    try:
        await streaming.stream_gpt(
            messages=msgs,
            system=_WIZARD_SYSTEM,
            on_token=_on_token,
            tier="cloud",      # 활성(클라우드) 프로바이더로 진행
            ce_mode=False,     # CE 게이트는 이미 비활성
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"LLM 호출 실패: {e}"}, status_code=502)

    reply, directives = _parse_directives(collected["text"])
    applied = _apply_directives(directives)
    return JSONResponse({"ok": True, "reply": reply, "directives": directives, "applied": applied})


def _parse_directives(text: str) -> tuple[str, list[dict]]:
    """```vega ...``` 코드펜스를 추출해 directive 로 파싱하고, 본문에서 제거."""
    import re
    directives: list[dict] = []
    pattern = re.compile(r"```vega\s*(.*?)```", re.DOTALL)
    for m in pattern.finditer(text):
        try:
            obj = json.loads(m.group(1).strip())
            if isinstance(obj, dict):
                directives.append(obj)
        except Exception:
            continue
    return pattern.sub("", text).strip(), directives


def _apply_directives(directives: list[dict]) -> list[str]:
    from pipeline.user_profile import load_profile, save_profile
    applied: list[str] = []
    profile = load_profile()
    changed = False
    for d in directives:
        if isinstance(d.get("set"), dict):
            for k, v in d["set"].items():
                if k in ("display_name", "role_summary", "company") and isinstance(v, str):
                    profile[k] = v.strip()
                    applied.append(k); changed = True
        if d.get("action") == "finish":
            profile["onboarded"] = True
            applied.append("onboarded"); changed = True
        elif d.get("action") == "google_auth":
            applied.append("google_auth_requested")
    if changed:
        save_profile(profile)
    return applied


# ── Slack 연동 단계 ───────────────────────────────────────────────────────────
# OAuth 자체는 server.py의 GET /slack/auth (새 탭) → GET /slack/callback 가 처리한다.
# 마법사는 아래 상태 엔드포인트를 폴링해 연결 완료를 감지한다.

@router.get("/api/onboarding/slack")
async def slack_status():
    """Slack 연동 상태. configured(빌드에 client.json 있음) + authenticated(user token 보유)."""
    try:
        from pipeline.auth import slack
        return JSONResponse({
            "configured": slack.is_configured(),
            "authenticated": slack.is_authenticated(),
            "team": slack.stored_team(),
        })
    except Exception as e:
        return JSONResponse({"configured": False, "authenticated": False, "error": str(e)})


# ── Google Cloud OAuth 단계 ──────────────────────────────────────────────────

class GoogleCredsPayload(BaseModel):
    client_id: str = ""
    client_secret: str = ""


@router.post("/api/onboarding/google/creds")
async def save_google_creds(payload: GoogleCredsPayload):
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
    """Google OAuth 동의 흐름 — 브라우저를 열어 refresh token 발급/저장."""
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

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _bootstrap_db)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"DB init failed: {e}"}, status_code=500)

    try:
        from pipeline.tools import patch_account_enum
        patch_account_enum()
    except Exception:
        pass

    return JSONResponse({"ok": True, "profile": load_profile()})


def _bootstrap_db() -> None:
    import sys
    from pathlib import Path
    root = Path(__file__).parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.init_user_db import init_db
    init_db()
