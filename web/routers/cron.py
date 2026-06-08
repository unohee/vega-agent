# Created: 2026-06-08
# Purpose: Cron 작업 API — 임의 프롬프트 예약 CRUD (INT-1407)
# Dependencies: pipeline/cron_jobs.py

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


@router.get("/api/cron")
async def list_cron():
    """예약된 cron 작업 목록."""
    from pipeline import cron_jobs
    return JSONResponse({"jobs": cron_jobs.list_jobs()})


class CronCreate(BaseModel):
    prompt: str = ""
    schedule: str = ""   # cron 표현식: 분 시 일 월 요일
    label: str = ""


@router.post("/api/cron")
async def create_cron(payload: CronCreate):
    """cron 작업 생성. schedule 유효성·프롬프트 검사 후 저장."""
    from pipeline import cron_jobs
    res = cron_jobs.create_job(payload.prompt, payload.schedule, payload.label)
    if res.get("error"):
        return JSONResponse({"ok": False, "error": res["error"]}, status_code=400)
    return JSONResponse({"ok": True, "job": res})


class CronToggle(BaseModel):
    enabled: bool = True


@router.post("/api/cron/{job_id}/toggle")
async def toggle_cron(job_id: str, payload: CronToggle):
    from pipeline import cron_jobs
    res = cron_jobs.set_enabled(job_id, payload.enabled)
    if res.get("error"):
        return JSONResponse({"ok": False, "error": res["error"]}, status_code=404)
    return JSONResponse(res)


@router.delete("/api/cron/{job_id}")
async def delete_cron(job_id: str):
    from pipeline import cron_jobs
    res = cron_jobs.delete_job(job_id)
    if res.get("error"):
        return JSONResponse({"ok": False, "error": res["error"]}, status_code=404)
    return JSONResponse(res)
