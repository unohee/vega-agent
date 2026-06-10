# Created: 2026-05-27
# Updated: 2026-05-31 — 멀티 프로바이더 설치 마법사. 프로바이더 목록→선택→해당 인증.
#   지원: ChatGPT(PKCE OAuth), Anthropic(API 키), OpenAI(API 키),
#         OpenRouter(API 키), 로컬·온프레미스(OpenAI 호환 URL). + Google Cloud OAuth 단계.
# Purpose: Onboarding / install-wizard API

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Request
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


# ── 워크플레이스 플러그인 카탈로그 ───────────────────────────────────────────
# status: "available"(지금 연동 가능) | "coming_soon"(UI에 표시하되 비활성)
PLUGIN_CATALOG = [
    {
        "id": "google",
        "label": "Google Workspace",
        "desc": "Gmail·Calendar·Drive 도구를 연결합니다.",
        "icon": "G",
        "auth": "oauth",
        "status": "available",
        "status_endpoint": "/api/onboarding/google",
        "auth_path": "/google/auth",
    },
    {
        "id": "slack",
        "label": "Slack",
        "desc": "채널·DM 읽기 및 검색.",
        "icon": "S",
        "auth": "oauth",
        "status": "available",
        "status_endpoint": "/api/onboarding/slack",
        "auth_path": "/slack/auth",
    },
    {
        "id": "superthread",
        "label": "Superthread",
        "desc": "보드·카드 읽기 및 관리.",
        "icon": "T",
        "auth": "oauth",
        "status": "available",
        "status_endpoint": "/api/onboarding/superthread",
        "auth_path": "/superthread/auth",
    },
    {
        "id": "airtable",
        "label": "Airtable",
        "desc": "베이스·레코드 조회 및 관리.",
        "icon": "A",
        "auth": "key",
        "status": "coming_soon",
    },
    {
        "id": "notion",
        "label": "Notion",
        "desc": "페이지·데이터베이스 읽기·쓰기.",
        "icon": "N",
        "auth": "oauth",
        "status": "coming_soon",
    },
    {
        "id": "github",
        "label": "GitHub",
        "desc": "이슈·PR·코드 검색.",
        "icon": "GH",
        "auth": "oauth",
        "status": "coming_soon",
    },
]


def _plugin_authenticated(pid: str) -> bool:
    """플러그인 현재 인증 여부(값은 노출 안 함)."""
    try:
        if pid == "google":
            from pipeline.auth import google
            return google.is_authenticated()
        if pid == "slack":
            from pipeline.auth import slack
            return slack.is_authenticated()
        if pid == "superthread":
            from pipeline.auth import superthread
            return superthread.is_authenticated()
    except Exception:
        pass
    return False


def _plugin_configured(pid: str) -> bool:
    """빌드에 해당 플러그인 클라이언트 시크릿이 포함됐는지."""
    try:
        if pid == "google":
            from pipeline.auth import google
            return google.is_configured()
        if pid == "slack":
            from pipeline.auth import slack
            return slack.is_configured()
        if pid == "superthread":
            return True  # public client — 항상 configured
    except Exception:
        pass
    return False


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
    profile = load_profile()
    try:
        from pipeline.auth import google as _g
        has_google = _g.is_authenticated()
    except Exception:
        has_google = False
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
        "plugins": [
            {
                **p,
                "configured": _plugin_configured(p["id"]),
                "authenticated": _plugin_authenticated(p["id"]),
            }
            for p in PLUGIN_CATALOG
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

규칙:
- 답변은 2~3문장 이내로 짧게. 한국어로.
- 한 번에 질문 하나만. 사용자가 답하면 다음으로 넘어간다.
- 필드를 확정했으면 그 턴 응답 맨 끝에 한 줄로 다음 JSON 을 ```vega 코드펜스로 감싼다(사용자에겐 안 보임):
  ```vega
  {"set": {"display_name": "홍길동"}}
  ```
- 모든 필드(이름·역할·회사)를 다 수집하거나 사용자가 건너뛰기를 원하면:
  짧은 환영 인사를 하고 "다음으로 연결할 서비스를 선택할게요."라고 안내한 뒤
  ```vega {"action": "finish"} ``` 를 출력한다.
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
            applied.append("finish")
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


# ── Superthread 연동 단계 ─────────────────────────────────────────────────────
# OAuth 자체는 server.py의 GET /superthread/auth (새 탭) → GET /superthread/callback.
# Superthread 는 public client(ocstcli)라 빌드 종속 client.json 이 없어 항상 configured.

@router.get("/api/onboarding/superthread")
async def superthread_status():
    """Superthread 연동 상태. authenticated(유효 PAT 보유) 여부."""
    try:
        from pipeline.auth import superthread
        return JSONResponse({
            "configured": True,
            "authenticated": superthread.is_authenticated(),
        })
    except Exception as e:
        return JSONResponse({"configured": False, "authenticated": False, "error": str(e)})


# ── Google 연동 단계 ─────────────────────────────────────────────────────────
# OAuth 자체는 server.py의 GET /google/auth (브라우저) → GET /google/callback.
# Slack 과 동일: 내장 google_oauth_client.json 을 쓰므로 사용자는 입력 없이 로그인만.

@router.get("/api/onboarding/google")
async def google_status():
    """Google 연동 상태. configured(빌드에 client.json 있음) + authenticated(refresh_token 보유)."""
    try:
        from pipeline.auth import google
        return JSONResponse({
            "configured": google.is_configured(),
            "authenticated": google.is_authenticated(),
            "email": google.stored_email(),
        })
    except Exception as e:
        return JSONResponse({"configured": False, "authenticated": False, "error": str(e)})


# ── 연결 해제 ─────────────────────────────────────────────────────────────────
# 설정 창 "연결" 패널에서 사용. Keychain 토큰만 삭제 — client.json 등 빌드 구성은 유지.

@router.post("/api/onboarding/{service}/disconnect")
async def disconnect_service(service: str):
    """워크스페이스 서비스 연결 해제 (slack/google/superthread). logout()이 Keychain 토큰 삭제."""
    if service not in ("slack", "google", "superthread"):
        return JSONResponse({"ok": False, "error": f"unknown service: {service}"}, status_code=404)
    try:
        import importlib
        mod = importlib.import_module(f"pipeline.auth.{service}")
        mod.logout()
        # 해제 즉시 워크스페이스 도구 가용성 캐시 반영 (pipeline/tool_registry.py, TTL 30s)
        from pipeline.tool_registry import invalidate_check_fn_cache
        invalidate_check_fn_cache()
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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


@router.post("/api/onboarding/reset")
async def reset_onboarding(request: Request):
    """온보딩 상태를 초기화해 다음 실행 시 설치 마법사로 되돌린다 — 빌드 디버깅용.

    confirm:true 필수. trash 경유(복구 가능, 직접 rm 금지 원칙).
    mode:
      "soft" (기본) — user_profile.json 만 제거. onboarded=False 가 되어 마법사 재진입.
                       DB·메모리·LLM 토큰·연동(Slack/Superthread)은 보존.
      "full"        — soft + LLM 프로바이더 설정·OAuth 토큰까지 제거. 완전한 첫 실행 상태.
    """
    import shutil
    import subprocess

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not body.get("confirm"):
        return JSONResponse(
            {"ok": False, "error": "confirm:true 필요 — 온보딩 상태를 되돌립니다"},
            status_code=400,
        )
    mode = (body.get("mode") or "soft").strip().lower()

    from pipeline.data_paths import data_dir
    d = data_dir()

    # soft: 온보딩 완료 플래그가 든 프로필만. full: + 프로바이더/토큰.
    targets = ["user_profile.json"]
    if mode == "full":
        targets += ["llm_providers.json", "chatgpt_token.json", "openai_oauth.json"]

    trash_bin = shutil.which("trash")
    removed, skipped = [], []
    for name in targets:
        p = d / name
        if not p.exists():
            continue
        try:
            if trash_bin:
                r = subprocess.run([trash_bin, str(p)], capture_output=True, text=True, timeout=30)
                (removed if r.returncode == 0 else skipped).append(name)
            else:
                skipped.append(name)
        except Exception:
            skipped.append(name)

    from pipeline.user_profile import is_onboarded
    return JSONResponse({
        "ok": not is_onboarded(),
        "mode": mode,
        "removed": removed,
        "skipped": skipped,
        "onboarded": is_onboarded(),
        "note": "다음 실행 시 설치 마법사로 시작합니다" if not is_onboarded()
                else ("trash CLI 없음 — 수동 삭제 필요" if skipped else "리셋 실패"),
    })


# ── 검색 엔드포인트 (SearXNG) — 설정 창 Tools & Keys + 첫 실행 안내 ──
class SearchEndpointPayload(BaseModel):
    url: str = ""
    key: str = ""


def _searxng_reachable(url: str, key: str) -> tuple[bool, str]:
    """SearXNG /search JSON 1회 호출로 도달성·인증 확인. (ok, detail)."""
    import urllib.parse
    import urllib.request

    base = url.rstrip("/")
    if not base:
        return False, "URL이 비어 있음"
    params = urllib.parse.urlencode({"q": "vega ping", "format": "json", "engines": "google"})
    headers = {"Accept": "application/json", "User-Agent": "VEGA/1.0"}
    if key:
        headers["X-VEGA-Key"] = key
    req = urllib.request.Request(f"{base}/search?{params}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                return True, "연결 성공"
            return False, f"HTTP {r.status}"
    except Exception as e:
        return False, str(e)[:200]


@router.get("/api/onboarding/search")
async def get_search_endpoint():
    """현재 검색 엔드포인트 설정 상태. 키 값 자체는 노출하지 않는다(저장 여부만)."""
    from pipeline.tools_web import _DEFAULT_SEARXNG_URL, _get_searxng_key, _get_searxng_url

    url = _get_searxng_url()
    return JSONResponse({
        "url": url,
        "has_key": bool(_get_searxng_key()),
        "is_default": url.rstrip("/") == _DEFAULT_SEARXNG_URL,
    })


@router.post("/api/onboarding/search")
async def configure_search_endpoint(payload: SearchEndpointPayload):
    """검색 엔드포인트 URL/키를 Keychain에 저장(런타임 즉시 반영). 저장 전 연결 테스트.
    키를 비워 보내면 기존 키 유지."""
    from pipeline import keychain
    from pipeline.tools_web import _get_searxng_key

    url = (payload.url or "").strip().rstrip("/")
    if not url:
        return JSONResponse({"ok": False, "error": "URL이 필요합니다."}, status_code=400)
    key = (payload.key or "").strip() or (_get_searxng_key() or "")

    ok, detail = _searxng_reachable(url, key)
    if not ok:
        return JSONResponse({"ok": False, "error": f"연결 실패: {detail}"}, status_code=400)

    keychain.set_secret("VEGA_SEARXNG_URL", url)
    os.environ["VEGA_SEARXNG_URL"] = url
    if (payload.key or "").strip():
        keychain.set_secret("VEGA_SEARXNG_KEY", key)
        os.environ["VEGA_SEARXNG_KEY"] = key
    return JSONResponse({"ok": True, "url": url, "detail": detail})


def _bootstrap_db() -> None:
    import sys
    from pathlib import Path
    root = Path(__file__).parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.init_user_db import init_db
    init_db()
