# Created: 2026-05-27
# Purpose: Path access security guard — shared by LLM tools (file_read) and the fs API
# Dependencies: pathlib (stdlib)

from __future__ import annotations

import os
import sys
from pathlib import Path


def _build_allowed_roots() -> list[Path]:
    roots = [Path.home()]
    if sys.platform == "win32":
        # Windows: 홈 상위 Users 디렉터리 (다중 사용자 프로필 하위 경로 접근)
        users_dir = Path.home().parent  # C:\Users
        if users_dir.name.lower() == "users":
            roots.append(users_dir)
        # Windows 임시 경로
        for env_var in ("TEMP", "TMP"):
            tmp = os.environ.get(env_var)
            if tmp:
                roots.append(Path(tmp))
        # 사용자가 명시적으로 추가한 extra 경로 (VEGA_EXTRA_PATHS 환경변수, ';' 구분)
        extra = os.environ.get("VEGA_EXTRA_PATHS", "")
        for p in extra.split(";"):
            p = p.strip()
            if p:
                roots.append(Path(p))
    else:
        roots += [
            Path("/tmp"),
            Path("/var/folders"),
            Path("/private/tmp"),         # macOS symlink target for /tmp
            Path("/private/var/folders"),
        ]
        # macOS/Linux extra 경로 (VEGA_EXTRA_PATHS, ':' 구분)
        extra = os.environ.get("VEGA_EXTRA_PATHS", "")
        for p in extra.split(":"):
            p = p.strip()
            if p:
                roots.append(Path(p))
    return roots


_ALLOWED_ROOTS: list[Path] = _build_allowed_roots()

# Blocked directory names (matched as path components)
_BLOCKED_DIRS: frozenset[str] = frozenset({
    ".ssh", ".gnupg", ".aws", ".azure", ".gcloud",
    "keychain", "Keychains",
})

# Blocked filenames (exact match)
_BLOCKED_NAMES: frozenset[str] = frozenset({
    ".env", ".env.local", ".env.production", ".env.development",
    ".netrc", ".npmrc", ".pypirc",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "authorized_keys", "known_hosts",
    "openai_oauth.json", "chatgpt_token.json",
    "credentials.json", "token.json",
})

# Blocked extensions
_BLOCKED_SUFFIXES: frozenset[str] = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".cer", ".crt",
})

# .env* glob pattern (prefix match)
_BLOCKED_PREFIXES: tuple[str, ...] = (".env",)

# Blocked filename patterns (substring match)
_BLOCKED_SUBSTRINGS: tuple[str, ...] = (
    "client_secret",
    "service_account",
    "refresh_token",
)


# ── 사용자 접근 정책 레이어 (access_policy.json) ───────────────────────────────
# 하드코딩 기본값 위에 사용자가 설정창에서 조정하는 allowlist/denylist 를 얹는다.
# 우선순위: 하드 denylist(불변) > 사용자 denylist > allowlist. 시크릿/키 차단은
# 사용자가 풀 수 없다(안전 보장). 사용자는 (a) 허용 루트 추가, (b) 추가 차단 경로
# 지정만 할 수 있다. 캐시는 mtime 으로 무효화해 설정창 변경이 즉시 반영된다.
_policy_cache: dict | None = None
_policy_mtime: float = 0.0


def _load_policy() -> dict:
    """access_policy.json 로드 — {allow_roots: [...], deny_paths: [...]}.

    파일 없으면 빈 정책. mtime 기반 캐시로 매 호출 디스크 IO 회피하되 변경 즉시 반영."""
    global _policy_cache, _policy_mtime
    try:
        from pipeline.data_paths import access_policy_path
        p = access_policy_path()
        if not p.exists():
            _policy_cache = {"allow_roots": [], "deny_paths": []}
            return _policy_cache
        mtime = p.stat().st_mtime
        if _policy_cache is not None and mtime == _policy_mtime:
            return _policy_cache
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        _policy_cache = {
            "allow_roots": [str(x) for x in data.get("allow_roots", [])],
            "deny_paths": [str(x) for x in data.get("deny_paths", [])],
        }
        _policy_mtime = mtime
        return _policy_cache
    except Exception:
        # 정책 파일이 깨져도 하드코딩 기본값으로 안전하게 동작
        return {"allow_roots": [], "deny_paths": []}


def _user_allow_roots() -> list[Path]:
    out: list[Path] = []
    for r in _load_policy().get("allow_roots", []):
        try:
            out.append(Path(r).expanduser().resolve())
        except Exception:
            continue
    return out


def _user_deny_paths() -> list[Path]:
    out: list[Path] = []
    for r in _load_policy().get("deny_paths", []):
        try:
            out.append(Path(r).expanduser().resolve())
        except Exception:
            continue
    return out


def guard_path(path: str) -> Path:
    """Validate path: resolve symlinks, check against allowed roots and sensitive patterns.

    Raises PermissionError on violation.
    Callers:
      - web/routers/fs.py → JSONResponse(403)
      - pipeline/tools_google.py (file_read) → returns error string
    Layered policy: 하드 denylist(불변) > 사용자 denylist > allowlist(기본+사용자).
    """
    p = Path(path).expanduser().resolve()

    # 사용자 denylist — 하드 denylist 다음 우선. 사용자가 "이 폴더 막아줘" 한 경로.
    for deny in _user_deny_paths():
        if p == deny or p.is_relative_to(deny):
            raise PermissionError(f"사용자 정책으로 차단된 경로: {p}")

    # allowlist — 기본 허용 루트 + 사용자가 추가한 루트
    allowed_roots = _ALLOWED_ROOTS + _user_allow_roots()
    if not any(p == root or p.is_relative_to(root) for root in allowed_roots):
        raise PermissionError(f"접근 금지 경로: {p}")

    # Check path components for blocked directory names
    for part in p.parts:
        if part in _BLOCKED_DIRS:
            raise PermissionError(f"민감 디렉터리 접근 차단: {part}")

    name = p.name
    name_lower = name.lower()

    # Exact filename block
    if name in _BLOCKED_NAMES or name_lower in _BLOCKED_NAMES:
        raise PermissionError(f"민감 파일 접근 차단: {name}")

    # Extension block
    if p.suffix.lower() in _BLOCKED_SUFFIXES:
        raise PermissionError(f"민감 확장자 접근 차단: {p.suffix}")

    # .env* prefix block (.env.local, .env.test, etc.)
    for prefix in _BLOCKED_PREFIXES:
        if name_lower.startswith(prefix):
            raise PermissionError(f"민감 파일 접근 차단: {name}")

    # Sensitive keyword substring check
    for sub in _BLOCKED_SUBSTRINGS:
        if sub in name_lower:
            raise PermissionError(f"민감 파일명 패턴 차단: {name}")

    return p


def is_allowed(path: str) -> bool:
    """guard_path 의 bool 버전 — 예외 없이 허용 여부만 반환 (UI 미리보기용)."""
    try:
        guard_path(path)
        return True
    except PermissionError:
        return False


# ── 정책 조회/편집 (설정창 API·도구에서 호출) ──────────────────────────────────

def get_policy() -> dict:
    """현재 접근 정책 전체 반환 — 하드코딩 기본값 + 사용자 레이어.

    설정창이 "무엇이 허용/차단되는지" 가시화하는 데 쓴다. 하드 항목은 readonly."""
    pol = _load_policy()
    return {
        "default_allow_roots": [str(r) for r in _ALLOWED_ROOTS],
        "user_allow_roots": pol.get("allow_roots", []),
        "user_deny_paths": pol.get("deny_paths", []),
        "hard_blocked": {
            "dirs": sorted(_BLOCKED_DIRS),
            "names": sorted(_BLOCKED_NAMES),
            "suffixes": sorted(_BLOCKED_SUFFIXES),
        },
    }


def set_policy(allow_roots: list[str] | None = None,
               deny_paths: list[str] | None = None) -> dict:
    """사용자 allowlist/denylist 저장. None 인 필드는 기존 값 유지.

    하드코딩 denylist(시크릿/키)는 건드릴 수 없다 — 안전 보장. 저장 후 다음
    guard_path 호출부터 mtime 캐시 무효화로 즉시 반영된다."""
    import json
    from pipeline.data_paths import access_policy_path
    cur = _load_policy()
    new = {
        "allow_roots": list(allow_roots) if allow_roots is not None
                       else cur.get("allow_roots", []),
        "deny_paths": list(deny_paths) if deny_paths is not None
                      else cur.get("deny_paths", []),
    }
    p = access_policy_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    return get_policy()
