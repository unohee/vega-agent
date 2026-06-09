"""
공유 런타임 상태 — server.py와 라우터 모듈이 함께 참조하는 전역 변수.

모든 dict는 모듈 임포트 시 한 번 생성되므로 어느 쪽에서 수정해도
동일 객체를 바라본다 (Python 모듈 싱글턴 보장).
"""
from __future__ import annotations

import asyncio
from pathlib import Path


# ── 세션 히스토리 캐시 ─────────────────────────────────────────────────────────
_SESSION_HISTORY: dict[str, list[dict]] = {}

# ── 모드 토글 ──────────────────────────────────────────────────────────────────
_PLAN_MODE: dict[str, bool] = {}
_RESEARCH_MODE: dict[str, bool] = {}
_YOLO_MODE: dict[str, bool] = {}
_GOAL_MODE: dict[str, bool] = {}

# ── YOLO 전역 플래그 (디스크 영속) ────────────────────────────────────────────

def _yolo_flag_path() -> Path:
    try:
        from pipeline.data_paths import data_dir
        return data_dir() / "yolo_global.flag"
    except Exception:
        return Path(__file__).parent.parent / "data" / "yolo_global.flag"


def _load_yolo_global() -> bool:
    try:
        return _yolo_flag_path().exists()
    except Exception:
        return False


def save_yolo_global(on: bool) -> None:
    try:
        p = _yolo_flag_path()
        if on:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("1")
        elif p.exists():
            p.unlink()
    except Exception:
        pass


_YOLO_GLOBAL: bool = _load_yolo_global()


def yolo_on(sid: str) -> bool:
    return _YOLO_GLOBAL or bool(_YOLO_MODE.get(sid))


# ── 태스크 레지스트리 ──────────────────────────────────────────────────────────
_TASK_REGISTRY: dict[str, dict] = {}

# ── 접근 레벨 ─────────────────────────────────────────────────────────────────
_ACCESS: dict[str, str] = {}
_ENT_KEY_KC = "vega-enterprise-keys"

_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "0.0.0.0"}


def load_enterprise_keys() -> frozenset[str]:
    try:
        from pipeline.keychain import get_secret
        raw = get_secret(_ENT_KEY_KC, service=_ENT_KEY_KC) or ""
        return frozenset(k.strip() for k in raw.split(",") if k.strip())
    except Exception:
        return frozenset()


# ── 하트비트 / 오토파일럿 ─────────────────────────────────────────────────────
HEARTBEAT_INTERVAL = 120.0
HEARTBEAT_STALL_SEC = 300.0
HEARTBEAT_MAX_RESUMES = 3
HEARTBEAT_DB_MAX_RESUMES = 20
_heartbeat_resumes: dict[str, int] = {}


def _autopilot_path() -> Path:
    try:
        from pipeline.data_paths import data_dir
        return data_dir() / "autopilot_sessions.json"
    except Exception:
        return Path(__file__).parent.parent / "data" / "autopilot_sessions.json"


def _load_autopilot() -> dict:
    try:
        import json as _json
        p = _autopilot_path()
        if p.exists():
            return _json.loads(p.read_text())
    except Exception:
        pass
    return {}


def _save_autopilot(data: dict) -> None:
    try:
        import json as _json
        p = _autopilot_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def autopilot_register(sid: str) -> None:
    data = _load_autopilot()
    if sid not in data:
        data[sid] = {"resumes": 0}
        _save_autopilot(data)


def autopilot_unregister(sid: str) -> None:
    data = _load_autopilot()
    if sid in data:
        data.pop(sid, None)
        _save_autopilot(data)


# ── 워치독 임계값 ─────────────────────────────────────────────────────────────
WATCHDOG_IDLE_DEFAULT = 60.0
WATCHDOG_IDLE_LONG = 300.0


def watchdog_idle_for(sid: str) -> float:
    if yolo_on(sid) or _GOAL_MODE.get(sid) or _RESEARCH_MODE.get(sid):
        return WATCHDOG_IDLE_LONG
    return WATCHDOG_IDLE_DEFAULT
