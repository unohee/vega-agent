# Created: 2026-06-08
# Purpose: 임의 프롬프트 cron 작업 — CRUD + cron 표현식 다음 실행 시각 (INT-1407)
# Dependencies: croniter, pipeline/data_paths.py

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def _path() -> Path:
    try:
        from pipeline.data_paths import data_dir
        return data_dir() / "cron_jobs.json"
    except Exception:
        return Path(__file__).parent.parent / "data" / "cron_jobs.json"


def _load() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(jobs: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def _valid_cron(expr: str) -> bool:
    try:
        from croniter import croniter
        return croniter.is_valid(expr)
    except Exception:
        return False


def _next_run(expr: str, base: datetime | None = None) -> str | None:
    """cron 표현식의 다음 실행 시각(KST ISO). base=None이면 호출 시점 기준."""
    try:
        from croniter import croniter
        b = base or datetime.now(KST)
        return croniter(expr, b).get_next(datetime).isoformat()
    except Exception:
        return None


def list_jobs() -> list[dict]:
    """모든 cron 작업."""
    return _load()


def create_job(prompt: str, schedule: str, label: str = "", *, now_iso: str | None = None) -> dict:
    """cron 작업 생성. schedule=cron 표현식(분 시 일 월 요일). 반환: 생성된 작업 또는 error."""
    prompt = (prompt or "").strip()
    schedule = (schedule or "").strip()
    if not prompt:
        return {"error": "프롬프트가 비어 있습니다."}
    if not _valid_cron(schedule):
        return {"error": f"유효하지 않은 cron 표현식: {schedule}"}
    base = None
    if now_iso:
        try:
            base = datetime.fromisoformat(now_iso)
        except Exception:
            base = None
    job = {
        "id": uuid.uuid4().hex[:12],
        "label": label.strip() or prompt[:40],
        "prompt": prompt,
        "schedule": schedule,
        "enabled": True,
        "created_at": (now_iso or datetime.now(KST).isoformat()),
        "next_run": _next_run(schedule, base),
        "last_run": None,
        "last_status": None,
    }
    jobs = _load()
    jobs.append(job)
    _save(jobs)
    return job


def delete_job(job_id: str) -> dict:
    jobs = _load()
    new = [j for j in jobs if j.get("id") != job_id]
    if len(new) == len(jobs):
        return {"error": "작업을 찾을 수 없음"}
    _save(new)
    return {"ok": True, "deleted": job_id}


def set_enabled(job_id: str, enabled: bool) -> dict:
    jobs = _load()
    for j in jobs:
        if j.get("id") == job_id:
            j["enabled"] = enabled
            _save(jobs)
            return {"ok": True, "id": job_id, "enabled": enabled}
    return {"error": "작업을 찾을 수 없음"}


def due_jobs(now: datetime | None = None) -> list[dict]:
    """현재 시각 기준 실행해야 할(enabled + next_run 도달) 작업 목록."""
    now = now or datetime.now(KST)
    out = []
    for j in _load():
        if not j.get("enabled"):
            continue
        nr = j.get("next_run")
        if not nr:
            continue
        try:
            if datetime.fromisoformat(nr) <= now:
                out.append(j)
        except Exception:
            continue
    return out


def mark_run(job_id: str, status: str, *, now: datetime | None = None) -> None:
    """실행 후 last_run/last_status 기록 + next_run 재계산."""
    now = now or datetime.now(KST)
    jobs = _load()
    for j in jobs:
        if j.get("id") == job_id:
            j["last_run"] = now.isoformat()
            j["last_status"] = status
            j["next_run"] = _next_run(j["schedule"], now)
            break
    _save(jobs)
