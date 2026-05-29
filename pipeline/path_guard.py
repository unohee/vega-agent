# Created: 2026-05-27
# Purpose: Path access security guard — shared by LLM tools (file_read) and the fs API
# Dependencies: pathlib (stdlib)

from __future__ import annotations

from pathlib import Path

_ALLOWED_ROOTS: list[Path] = [
    Path.home(),
    Path("/tmp"),
    Path("/var/folders"),
    Path("/private/tmp"),         # macOS symlink target for /tmp
    Path("/private/var/folders"),
]

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

    # Check against allowed roots
    p_str = str(p)
    if not any(p_str.startswith(str(root)) for root in _ALLOWED_ROOTS):
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
