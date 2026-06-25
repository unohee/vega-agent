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
_LOAD_MODE: dict[str, str] = {}  # sid -> light|standard|heavy (absent = auto)
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

# loopback 주소 집합. "0.0.0.0"은 제거했다 — 이는 바인드 와일드카드이지
# peer 주소가 아니므로 loopback 판정에 넣으면 매치 폭만 넓힌다 (INT-1468 H1).
_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}


def _trusted_proxy_enabled() -> bool:
    """X-Forwarded-For 를 신뢰할지. 기본 비활성 — 클라이언트가 보낸 XFF 로
    loopback/원격 판정을 우회하는 것을 막는다(INT-1468 H1). 사용자가 의도적으로
    리버스 프록시 뒤에 둘 때만 VEGA_TRUSTED_PROXY=1 로 켠다."""
    import os
    return os.environ.get("VEGA_TRUSTED_PROXY", "").strip().lower() in ("1", "true", "yes")


def _client_host(request) -> str:
    """판정에 쓸 peer 주소. 신뢰 프록시 모드일 때만 XFF 첫 값을 채택하고,
    그 외에는 위조 불가능한 실제 소켓 peer(request.client.host)만 신뢰한다."""
    if _trusted_proxy_enabled():
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return fwd
    return request.client.host if request.client else "127.0.0.1"


def is_loopback_host(host: str) -> bool:
    return host in _LOOPBACK or host.startswith("127.") or host.startswith("::ffff:127.")


def is_loopback(request) -> bool:
    """루프백(로컬) 연결 여부. XFF 는 신뢰 프록시 모드에서만 반영(INT-1468 H1)."""
    return is_loopback_host(_client_host(request))


def _tailscale_extra_cidrs() -> list:
    """추가 허용 CIDR (VEGA_REMOTE_ALLOW_CIDRS, 쉼표 구분). 비면 빈 목록."""
    import os
    import ipaddress
    raw = os.environ.get("VEGA_REMOTE_ALLOW_CIDRS", "").strip()
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return out


# Tailscale 가 노드에 할당하는 CGNAT 대역 (100.64.0.0/10) + IPv6 ULA (fd7a:115c:a1e0::/48).
# 사용자가 "tailscale 원격만 허용"을 요구 — 이 대역의 peer 는 원격이라도 허용한다.
_TAILSCALE_CIDRS_RAW = ["100.64.0.0/10", "fd7a:115c:a1e0::/48"]


def is_remote_allowed(request) -> bool:
    """원격 침입 차단 게이트(INT-1468 H2). 다음 중 하나면 허용:
      - loopback (로컬 앱/터미널)
      - Tailscale 대역 peer (사용자 요구: tailscale 원격만 허용)
      - VEGA_REMOTE_ALLOW_CIDRS 에 포함된 peer
      - 유효한 enterprise 키 보유
    그 외 모든 원격 접속은 거부한다."""
    import ipaddress
    if is_loopback(request):
        return True
    host = _client_host(request)
    try:
        ip = ipaddress.ip_address(host.replace("::ffff:", "") if "." in host else host)
        allow = [ipaddress.ip_network(c) for c in _TAILSCALE_CIDRS_RAW] + _tailscale_extra_cidrs()
        if any(ip in net for net in allow):
            return True
    except ValueError:
        pass
    key = request.headers.get("x-vega-key", "").strip()
    if key and key in load_enterprise_keys():
        return True
    return False


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
