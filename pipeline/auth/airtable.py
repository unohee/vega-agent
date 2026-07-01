# Created: 2026-06-18
# Purpose: Airtable PAT 인증 — keychain 저장/조회 (INT-1498).
#   Airtable 은 OAuth 가 아니라 Personal Access Token 방식. 사용자가 airtable.com
#   에서 PAT 를 발급해 온보딩(/api/onboarding/provider, auth="key")으로 입력하면
#   keychain(AIRTABLE_PERSONAL_ACCESS_TOKEN)에 저장된다. Slack/Superthread 의
#   auth 모듈과 동일한 is_authenticated() 게이트 인터페이스를 제공한다.
# Dependencies: pipeline.keychain
from __future__ import annotations

from pipeline import keychain as _kc

# onboarding entry 의 key_env 와 일치해야 한다. kyte-portal 과 동일 키명 사용.
KEY_ENV = "AIRTABLE_PERSONAL_ACCESS_TOKEN"
# logout tombstone (INT-2233): _kc.get 는 Keychain→.env→env 로 fallback 하므로,
# Keychain 만 지우면 .env/env 의 PAT 로 logout 후에도 인증 상태가 유지된다.
# logout 시 이 플래그를 세우고 token()이 우선 확인 → 재연결(onboarding save) 시 해제.
_LOGOUT_FLAG = KEY_ENV + "_logged_out"


def token() -> str | None:
    """저장된 Airtable PAT (Keychain → .env → 환경변수). logout 후엔 None (INT-2233)."""
    if _kc.get_secret(_LOGOUT_FLAG):
        return None
    return _kc.get(KEY_ENV) or None


def is_authenticated() -> bool:
    """PAT 보유 여부 — tool_registry 가용성 게이트가 호출."""
    return bool(token())


def is_configured() -> bool:
    """PAT 방식은 클라이언트 시크릿이 불필요 — 항상 설정 가능(사용자 키만 입력)."""
    return True


def logout() -> None:
    """저장된 PAT 제거 + logout 플래그 — .env/env fallback 까지 무력화 (INT-2233)."""
    _kc.delete_secret(KEY_ENV)
    _kc.set_secret(_LOGOUT_FLAG, "1")
