# Created: 2026-05-27
# Purpose: Onboarding API — GET (current profile) + POST (save profile + init DB)

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class OnboardingPayload(BaseModel):
    display_name: str = ""
    role_summary: str = ""
    company: str = ""
    email_accounts: list[dict] = []
    llm_provider_pref: str = ""


@router.get("/api/onboarding")
async def get_onboarding():
    """Return the current user_profile and whether onboarding is complete."""
    from pipeline.user_profile import load_profile, is_onboarded
    profile = load_profile()
    return JSONResponse({
        "onboarded": is_onboarded(),
        "profile": profile,
    })


@router.post("/api/onboarding")
async def post_onboarding(payload: OnboardingPayload):
    """Save profile and initialize DB. Marks onboarding as complete."""
    from pipeline.user_profile import save_profile, load_profile
    import asyncio, sys
    from pathlib import Path

    profile = load_profile()
    if payload.display_name:
        profile["display_name"] = payload.display_name.strip()
    if payload.role_summary:
        profile["role_summary"] = payload.role_summary.strip()
    if payload.company:
        profile["company"] = payload.company.strip()
    if payload.email_accounts:
        # Basic validation: key and email fields are required
        clean = []
        for acc in payload.email_accounts:
            key = (acc.get("key") or "").strip().lower()
            email = (acc.get("email") or "").strip()
            if key and email:
                clean.append({"key": key, "email": email, "label": acc.get("label") or key})
        profile["email_accounts"] = clean
    if payload.llm_provider_pref:
        profile["llm_provider_pref"] = payload.llm_provider_pref.strip()

    save_profile(profile)

    # Init DB (new user — idempotent if already exists)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _bootstrap_db)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"DB init failed: {e}"}, status_code=500)

    # Refresh account enum if server is already running
    try:
        from pipeline.tools import patch_account_enum
        patch_account_enum()
    except Exception:
        pass

    return JSONResponse({"ok": True, "profile": load_profile()})


def _bootstrap_db() -> None:
    """Call init_db() from scripts/init_user_db.py directly."""
    import sys
    from pathlib import Path
    root = Path(__file__).parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.init_user_db import init_db
    init_db()
