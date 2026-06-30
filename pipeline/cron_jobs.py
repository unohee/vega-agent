# Created: 2026-06-08
# Purpose: 임의 프롬프트 cron 작업 — CRUD + cron 표현식 다음 실행 시각 (INT-1407)
#          Sub-agent slot — is_slot=True인 cron job (INT-1418)
# Dependencies: croniter, pipeline/data_paths.py

from __future__ import annotations

import contextlib
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

_cron_lock_local = threading.Lock()


@contextlib.contextmanager
def _cron_lock():
    """load-modify-save 직렬화 (INT-2236) — UI/API create/toggle/delete 와 heartbeat
    mark_run 의 동시 변경에서 lost update 방지. 스레드 락 + 프로세스 간 fcntl flock(POSIX).
    Windows 는 fcntl 부재라 스레드 락만 적용."""
    with _cron_lock_local:
        try:
            import fcntl
        except ImportError:
            yield
            return
        from pipeline.data_paths import data_dir
        try:
            lock_path = data_dir() / "cron_jobs.lock"
        except Exception:
            lock_path = _path().with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(lock_path, "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(f, fcntl.LOCK_UN)
            finally:
                f.close()


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
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


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
    """일반 cron 작업 목록 (slot 제외)."""
    return [j for j in _load() if not j.get("is_slot")]


def list_slots() -> list[dict]:
    """Sub-agent slot 목록 (is_slot=True)."""
    return [j for j in _load() if j.get("is_slot")]


def _create_entry(
    prompt: str,
    schedule: str,
    label: str = "",
    *,
    now_iso: str | None = None,
    is_slot: bool = False,
    icon: str = "🤖",
) -> dict:
    """공통 작업 생성 내부 함수."""
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
    entry: dict = {
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
    if is_slot:
        entry["is_slot"] = True
        entry["icon"] = icon or "🤖"
        entry["last_session_id"] = None
    return entry


def create_job(prompt: str, schedule: str, label: str = "", *, now_iso: str | None = None) -> dict:
    """cron 작업 생성. schedule=cron 표현식(분 시 일 월 요일). 반환: 생성된 작업 또는 error."""
    entry = _create_entry(prompt, schedule, label, now_iso=now_iso, is_slot=False)
    if "error" in entry:
        return entry
    with _cron_lock():
        jobs = _load()
        jobs.append(entry)
        _save(jobs)
    return entry


def create_slot(
    prompt: str,
    schedule: str,
    label: str = "",
    icon: str = "🤖",
    *,
    now_iso: str | None = None,
) -> dict:
    """Sub-agent slot 생성. 기존 cron job과 동일한 스토리지를 공유하되 is_slot=True."""
    entry = _create_entry(prompt, schedule, label, now_iso=now_iso, is_slot=True, icon=icon)
    if "error" in entry:
        return entry
    with _cron_lock():
        jobs = _load()
        jobs.append(entry)
        _save(jobs)
    return entry


def delete_job(job_id: str) -> dict:
    with _cron_lock():
        jobs = _load()
        new = [j for j in jobs if j.get("id") != job_id]
        if len(new) == len(jobs):
            return {"error": "작업을 찾을 수 없음"}
        _save(new)
    return {"ok": True, "deleted": job_id}


def set_enabled(job_id: str, enabled: bool) -> dict:
    with _cron_lock():
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


def mark_run(job_id: str, status: str, *, now: datetime | None = None, session_id: str | None = None) -> None:
    """실행 후 last_run/last_status 기록 + next_run 재계산. slot이면 last_session_id도 기록."""
    now = now or datetime.now(KST)
    with _cron_lock():
        jobs = _load()
        for j in jobs:
            if j.get("id") == job_id:
                j["last_run"] = now.isoformat()
                j["last_status"] = status
                j["next_run"] = _next_run(j["schedule"], now)
                if j.get("is_slot") and session_id:
                    j["last_session_id"] = session_id
                break
        _save(jobs)
