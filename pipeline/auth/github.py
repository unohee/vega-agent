# Created: 2026-06-18
# Purpose: GitHub PAT 인증 — keychain 저장/조회 (INT-1498).
#   Airtable 와 동일한 key 방식. 사용자가 github.com 에서 PAT(classic 또는
#   fine-grained)를 발급해 온보딩(auth="key")으로 입력하면 keychain
#   (GITHUB_PERSONAL_ACCESS_TOKEN)에 저장된다.
# Dependencies: pipeline.keychain
from __future__ import annotations

from pipeline import keychain as _kc

KEY_ENV = "GITHUB_PERSONAL_ACCESS_TOKEN"
# logout tombstone (INT-2233) — Keychain→.env→env fallback 때문에 Keychain delete 만으론
# logout 후에도 인증 유지. 플래그로 token()을 차단하고 재연결(onboarding save) 시 해제.
_LOGOUT_FLAG = KEY_ENV + "_logged_out"


def token() -> str | None:
    """저장된 GitHub PAT (Keychain → .env → 환경변수). logout 후엔 None (INT-2233)."""
    if _kc.get_secret(_LOGOUT_FLAG):
        return None
    return _kc.get(KEY_ENV) or None


def is_authenticated() -> bool:
    return bool(token())


def is_configured() -> bool:
    """PAT 방식 — 클라이언트 시크릿 불필요."""
    return True


def logout() -> None:
    _kc.delete_secret(KEY_ENV)
    _kc.set_secret(_LOGOUT_FLAG, "1")
