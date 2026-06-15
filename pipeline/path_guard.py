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


def guard_path(path: str) -> Path:
    """Validate path: resolve symlinks, check against allowed roots and sensitive patterns.

    Raises PermissionError on violation.
    Callers:
      - web/routers/fs.py → JSONResponse(403)
      - pipeline/tools_google.py (file_read) → returns error string
    """
    p = Path(path).expanduser().resolve()

    # Check against allowed roots — use Path.is_relative_to to prevent /tmpfoo bypassing /tmp
    if not any(p == root or p.is_relative_to(root) for root in _ALLOWED_ROOTS):
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
