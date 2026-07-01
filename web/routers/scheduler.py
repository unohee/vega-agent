# Created: 2026-05-27
# Purpose: Background scheduler configuration API — LaunchAgent on/off + time adjustment (RES-222)
# Dependencies: subprocess (launchctl), fastapi

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()

_PLIST_MAP = {
    "morning_brief": Path.home() / "Library/LaunchAgents/com.unohee.vega.brief-morning.plist",
    "evening_review": Path.home() / "Library/LaunchAgents/com.unohee.vega.brief-evening.plist",
    "heartbeat": Path.home() / "Library/LaunchAgents/com.unohee.vega.heartbeat.plist",
}


def _job_label(key: str) -> str:
    plist = _PLIST_MAP.get(key)
    if not plist:
        return ""
    return plist.stem  # com.unohee.vega.brief-morning


def _is_loaded(label: str) -> bool:
    try:
        r = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _plist_schedule(plist: Path) -> dict | None:
    """Extract StartCalendarInterval from a plist file."""
    try:
        import plistlib
        with open(plist, "rb") as f:
            data = plistlib.load(f)
        cal = data.get("StartCalendarInterval", {})
        return {"hour": cal.get("Hour"), "minute": cal.get("Minute", 0)}
    except Exception:
        return None


@router.get("/api/scheduler/status")
async def scheduler_status():
    """Return the status of each scheduler entry."""
    result = {}
    for key, plist in _PLIST_MAP.items():
        label = _job_label(key)
        loaded = _is_loaded(label) if plist.exists() else False
        schedule = _plist_schedule(plist) if plist.exists() else None
        result[key] = {
            "label": label,
            "plist_exists": plist.exists(),
            "loaded": loaded,
            "schedule": schedule,
        }
    return JSONResponse(result)


class SchedulerToggle(BaseModel):
    job: str        # morning_brief | evening_review | heartbeat
    enable: bool


@router.post("/api/scheduler/toggle")
async def scheduler_toggle(payload: SchedulerToggle):
    """Load or unload a LaunchAgent."""
    plist = _PLIST_MAP.get(payload.job)
    if not plist:
        return JSONResponse({"ok": False, "error": f"unknown job: {payload.job}"}, status_code=400)
    if not plist.exists():
        return JSONResponse({"ok": False, "error": f"plist not found: {plist}"}, status_code=404)

    label = _job_label(payload.job)
    if payload.enable:
        r = subprocess.run(["launchctl", "load", str(plist)], capture_output=True, text=True, timeout=10)
    else:
        r = subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, text=True, timeout=10)

    ok = r.returncode == 0
    return JSONResponse({
        "ok": ok,
        "job": payload.job,
        "enabled": payload.enable,
        "stderr": r.stderr.strip() if not ok else "",
    })


class ScheduleTime(BaseModel):
    job: str    # morning_brief | evening_review
    hour: int   # 0-23
    minute: int = 0


@router.post("/api/scheduler/set-time")
async def scheduler_set_time(payload: ScheduleTime):
    """Update StartCalendarInterval and reload the LaunchAgent."""
    import plistlib

    plist = _PLIST_MAP.get(payload.job)
    if not plist or payload.job == "heartbeat":
        return JSONResponse({"ok": False, "error": "time cannot be changed for this job"}, status_code=400)
    if not plist.exists():
        return JSONResponse({"ok": False, "error": f"plist not found: {plist}"}, status_code=404)
    if not (0 <= payload.hour <= 23 and 0 <= payload.minute <= 59):
        return JSONResponse({"ok": False, "error": "invalid time"}, status_code=400)

    with open(plist, "rb") as f:
        data = plistlib.load(f)

    data["StartCalendarInterval"] = {"Hour": payload.hour, "Minute": payload.minute}
    with open(plist, "wb") as f:
        plistlib.dump(data, f)

    label = _job_label(payload.job)
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, timeout=5)
    # unload 는 미로드 상태면 non-zero 라 load 결과로만 성공 판정 (INT-2234) — 실패를
    # 무시하고 ok=true 를 반환하면 스케줄이 안 바뀌었는데 성공으로 오보된다.
    r_load = subprocess.run(["launchctl", "load", str(plist)], capture_output=True, text=True, timeout=5)
    if r_load.returncode != 0:
        return JSONResponse(
            {"ok": False, "job": payload.job, "error": (r_load.stderr or "launchctl load 실패").strip()},
            status_code=500,
        )

    return JSONResponse({
        "ok": True,
        "job": payload.job,
        "hour": payload.hour,
        "minute": payload.minute,
    })


@router.post("/api/scheduler/run-now")
async def scheduler_run_now(payload: SchedulerToggle):
    """Run immediately via launchctl kickstart."""
    label = _job_label(payload.job)
    if not label:
        return JSONResponse({"ok": False, "error": f"unknown job: {payload.job}"}, status_code=400)

    import pwd, os
    uid = os.getuid()
    r = subprocess.run(
        ["launchctl", "kickstart", f"gui/{uid}/{label}"],
        capture_output=True, text=True, timeout=10,
    )
    return JSONResponse({"ok": r.returncode == 0, "job": payload.job, "stderr": r.stderr.strip()})
