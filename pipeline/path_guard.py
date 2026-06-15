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
        # Windows: 홈 상위의 Users 디렉터리 (다중 사용자 공유 폴더 접근 허용)
        users_dir = Path.home().parent  # C:\Users
        if users_dir.name.lower() == "users":
            roots.append(users_dir)
        # Windows 임시 경로
        for env_var in ("TEMP", "TMP"):
            tmp = os.environ.get(env_var)
            if tmp:
                roots.append(Path(tmp))
        # 각 드라이브의 루트 — 파일 선택 다이얼로그가 드라이브 루트를 반환할 수 있음.
        # System32 등 민감 경로는 _BLOCKED_DIRS로 추가 차단한다.
        import string
        for drive in string.ascii_uppercase:
            p = Path(f"{drive}:\\")
            if p.exists():
                roots.append(p)
    else:
        roots += [
            Path("/tmp"),
            Path("/var/folders"),
            Path("/private/tmp"),         # macOS symlink target for /tmp
            Path("/private/var/folders"),
        ]
    return roots


_ALLOWED_ROOTS: list[Path] = _build_allowed_roots()

# Blocked directory names (matched as path components)
_BLOCKED_DIRS: frozenset[str] = frozenset({
    ".ssh", ".gnupg", ".aws", ".azure", ".gcloud",
    "keychain", "Keychains",
    # Windows 시스템 민감 경로 (드라이브 루트 허용 후 추가 차단)
    "Windows", "System32", "SysWOW64", "WinSxS",
    "Program Files", "Program Files (x86)",
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
