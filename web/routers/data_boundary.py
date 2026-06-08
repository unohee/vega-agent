# Created: 2026-06-08
# Purpose: 로컬-퍼스트 데이터 경계 — 데이터 요약·export(zip)·wipe (INT-1383)
# Dependencies: pipeline/data_paths, stdlib (zipfile, shutil)

from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
KST = ZoneInfo("Asia/Seoul")

# export/wipe 대상 — 개인 데이터 자산. 토큰은 별도(wipe 시 사용자 선택).
_DATA_ASSETS = [
    "agent.db", "vega.db", "contacts.db", "tool_telemetry.db", "run_log.db",
    "lancedb", "patches", "improvements.jsonl", "memory_settings.json",
    "persona.md", "user_profile.json", "widgets.json",
]
_TOKEN_ASSETS = [
    "chatgpt_token.json", "openai_oauth.json", "llm_providers.json",
]


def _data_dir() -> Path:
    from pipeline.data_paths import data_dir
    return data_dir()


def _size_of(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return 0


@router.get("/api/data/summary")
async def data_summary():
    """로컬 데이터 자산별 크기·존재 여부. '내 데이터는 어디에' 화면용."""
    d = _data_dir()
    assets = []
    total = 0
    for name in _DATA_ASSETS + _TOKEN_ASSETS:
        p = d / name
        if p.exists():
            sz = _size_of(p)
            total += sz
            assets.append({
                "name": name, "bytes": sz,
                "kind": "token" if name in _TOKEN_ASSETS else "data",
                "is_dir": p.is_dir(),
            })
    return JSONResponse({"data_dir": str(d), "assets": assets, "total_bytes": total})


@router.post("/api/data/export")
async def data_export(request: Request):
    """개인 데이터를 zip으로 묶어 data_dir()/exports/ 에 저장. 백업·이전용.
    토큰 포함 여부는 include_tokens(기본 False)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    include_tokens = bool(body.get("include_tokens", False))
    d = _data_dir()
    exports = d / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    # Date.now가 불가하므로 서버 시간 사용 (이 코드는 런타임 — 정상)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    out = exports / f"vega_export_{stamp}.zip"

    targets = list(_DATA_ASSETS)
    if include_tokens:
        targets += _TOKEN_ASSETS

    written = []
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in targets:
                p = d / name
                if not p.exists():
                    continue
                if p.is_file():
                    zf.write(p, name)
                    written.append(name)
                else:
                    for f in p.rglob("*"):
                        if f.is_file():
                            zf.write(f, str(f.relative_to(d)))
                    written.append(name + "/")
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "path": str(out), "bytes": out.stat().st_size,
                         "included": written, "tokens_included": include_tokens})


@router.post("/api/data/wipe")
async def data_wipe(request: Request):
    """개인 데이터 삭제. confirm:true 필수(확인 게이트). trash 경유(복구 가능).
    include_tokens(기본 False)면 토큰도 삭제."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not body.get("confirm"):
        return JSONResponse({"ok": False, "error": "confirm:true 필요 — 되돌리기 어려운 작업"}, status_code=400)
    include_tokens = bool(body.get("include_tokens", False))
    d = _data_dir()
    targets = list(_DATA_ASSETS)
    if include_tokens:
        targets += _TOKEN_ASSETS

    # trash 경유 삭제 (복구 가능). trash CLI가 없으면 거부 — 직접 rm 금지(가드 원칙).
    trash_bin = shutil.which("trash")
    removed, skipped = [], []
    for name in targets:
        p = d / name
        if not p.exists():
            continue
        try:
            if trash_bin:
                import subprocess
                r = subprocess.run([trash_bin, str(p)], capture_output=True, text=True, timeout=30)
                if r.returncode == 0:
                    removed.append(name)
                else:
                    skipped.append(name)
            else:
                skipped.append(name)
        except Exception:
            skipped.append(name)
    ok = bool(removed) or not targets
    return JSONResponse({"ok": ok, "removed": removed, "skipped": skipped,
                         "note": "휴지통으로 이동(복구 가능)" if trash_bin else "trash CLI 없음 — 수동 삭제 필요"})
