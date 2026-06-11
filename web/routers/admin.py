from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from web.state import _ENT_KEY_KC, is_loopback, load_enterprise_keys

router = APIRouter()


def _require_local(request: Request) -> None:
    # INT-1468 H1: loopback 판정은 state.is_loopback 단일 출처(클라이언트 XFF 미신뢰).
    if not is_loopback(request):
        raise HTTPException(status_code=403, detail="Only local connections are allowed.")


@router.get("/api/admin/keys")
async def admin_keys_list(request: Request):
    _require_local(request)
    keys = sorted(load_enterprise_keys())
    return JSONResponse({"keys": keys, "count": len(keys)})


@router.post("/api/admin/keys")
async def admin_keys_add(request: Request):
    _require_local(request)
    body = await request.json()
    key = (body.get("key") or "").strip()
    if not key:
        return JSONResponse({"error": "key 필드 필요"}, status_code=400)
    if not key.startswith("vk_"):
        return JSONResponse({"error": "키는 vk_ 로 시작해야 합니다"}, status_code=400)
    from pipeline.keychain import get_secret, set_secret
    existing = set(k.strip() for k in (get_secret(_ENT_KEY_KC, service=_ENT_KEY_KC) or "").split(",") if k.strip())
    existing.add(key)
    set_secret(_ENT_KEY_KC, ",".join(sorted(existing)), service=_ENT_KEY_KC)
    return JSONResponse({"ok": True, "key": key, "total": len(existing)})


@router.delete("/api/admin/keys/{key}")
async def admin_keys_delete(key: str, request: Request):
    _require_local(request)
    from pipeline.keychain import get_secret, set_secret
    existing = set(k.strip() for k in (get_secret(_ENT_KEY_KC, service=_ENT_KEY_KC) or "").split(",") if k.strip())
    existing.discard(key)
    set_secret(_ENT_KEY_KC, ",".join(sorted(existing)), service=_ENT_KEY_KC)
    return JSONResponse({"ok": True, "remaining": len(existing)})
