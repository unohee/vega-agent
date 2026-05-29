# Created: 2026-05-27
# Purpose: User profile loader — all system prompt builders read from here.
#   Returns a generic placeholder if user_profile.json is missing on first run.
#   After onboarding, the filled profile injects persona/email/role into system prompts.
# Dependencies: stdlib

from __future__ import annotations

import json
from typing import Any

from pipeline.data_paths import user_profile_path

# Empty profile fallback — used before onboarding or when keys are missing.
DEFAULT_PROFILE: dict[str, Any] = {
    "display_name": "사용자",
    "role_summary": "VEGA 사용자",
    "company": "",
    "email_accounts": [],   # [{"key": "personal", "email": "...", "label": "Personal"}, ...]
    "llm_provider_pref": "",  # active provider name in llm_providers.json
    "onboarded": False,
}


def load_profile() -> dict[str, Any]:
    """Read user_profile.json. Returns DEFAULT_PROFILE if the file is missing or fails to parse.
    Callers should use .get() rather than assuming key presence.
    """
    p = user_profile_path()
    if not p.exists():
        return dict(DEFAULT_PROFILE)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULT_PROFILE)
        # Merge defaults (fill in missing keys)
        merged = dict(DEFAULT_PROFILE)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_PROFILE)


def save_profile(profile: dict[str, Any]) -> None:
    """Save user_profile.json. Automatically sets onboarded=True."""
    p = user_profile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    out = dict(DEFAULT_PROFILE)
    out.update(profile or {})
    out["onboarded"] = True
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def is_onboarded() -> bool:
    return load_profile().get("onboarded", False)


def display_name() -> str:
    """Display name for system prompts — falls back to a generic name if no persona is set."""
    return load_profile().get("display_name") or "사용자"


def role_summary() -> str:
    """Short one-line role summary for system prompt context. May be an empty string."""
    return (load_profile().get("role_summary") or "").strip()


def email_accounts() -> list[dict[str, str]]:
    """List of registered email accounts. May be empty (before onboarding or not configured)."""
    accs = load_profile().get("email_accounts") or []
    if not isinstance(accs, list):
        return []
    out: list[dict[str, str]] = []
    for a in accs:
        if not isinstance(a, dict):
            continue
        key = (a.get("key") or "").strip().lower()
        email = (a.get("email") or "").strip()
        if not key or not email:
            continue
        out.append({"key": key, "email": email, "label": a.get("label") or key})
    return out


def email_for(key: str) -> str | None:
    """Resolve account key to email address. Returns None if not found."""
    key = (key or "").strip().lower()
    for a in email_accounts():
        if a["key"] == key:
            return a["email"]
    return None


def default_email_key() -> str | None:
    """Key of the first registered account (default account). Returns None if none registered."""
    accs = email_accounts()
    return accs[0]["key"] if accs else None
