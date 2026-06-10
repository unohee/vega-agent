# Created: 2026-05-27
# Purpose: macOS Keychain wrapper — secret read/write (RES-226)
# Dependencies: subprocess (security CLI), stdlib only
# Test Status: verified

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SERVICE = "VEGA"

# `security` CLI는 macOS 전용. Windows/Linux에선 모든 Keychain 연산을 "없음"으로
# 처리해 get()이 .env → 환경변수 폴백 체인으로 넘어가게 한다 (INT-1438).
# 가드 없이 호출하면 FileNotFoundError가 모든 keychain.get() 사용처로 전파된다.
_HAS_KEYCHAIN = sys.platform == "darwin"


def _security(*args: str, input_text: str | None = None) -> tuple[int, str]:
    """Invoke the macOS `security` CLI. Returns (returncode, stdout)."""
    if not _HAS_KEYCHAIN:
        return 1, "keychain unavailable on this platform"
    result = subprocess.run(
        ["security", *args],
        capture_output=True,
        text=True,
        input=input_text,
        timeout=5,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def get_secret(key: str, service: str = _SERVICE) -> str | None:
    """Look up a secret in Keychain. Returns None if not found."""
    rc, out = _security("find-generic-password", "-s", service, "-a", key, "-w")
    if rc == 0:
        return out.strip() or None
    return None


def set_secret(key: str, value: str, service: str = _SERVICE) -> bool:
    """Store a secret in Keychain (updates if already present)."""
    # Delete existing entry then re-add (no update command available)
    _security("delete-generic-password", "-s", service, "-a", key)
    rc, _ = _security(
        "add-generic-password", "-s", service, "-a", key, "-w", value
    )
    return rc == 0


def delete_secret(key: str, service: str = _SERVICE) -> bool:
    """Delete a secret from Keychain."""
    rc, _ = _security("delete-generic-password", "-s", service, "-a", key)
    return rc == 0


def _env_file_paths() -> list[Path]:
    """탐색할 .env 경로(우선순위 순).

    배포본(.app)에서 Path(__file__) 은 PyInstaller 번들 임시경로(_MEIPASS)를
    가리켜 사용자가 .env 를 둘 수 없다. 따라서 영속 사용자 데이터 루트의 .env 를
    먼저 본다. 레포 루트 .env 는 개발 환경 폴백.
    """
    paths: list[Path] = []
    try:
        from pipeline.data_paths import data_dir
        paths.append(data_dir() / ".env")
    except Exception:
        pass
    paths.append(Path(__file__).parent.parent / ".env")  # 개발용 레포 루트
    return paths


def _parse_env(env_file: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not env_file.exists():
        return result
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _load_env_file() -> dict[str, str]:
    """모든 후보 .env 를 병합해 반환(앞 경로 우선)."""
    merged: dict[str, str] = {}
    for path in reversed(_env_file_paths()):  # 뒤(낮은 우선순위)부터 채우고 앞이 덮어쓰게
        merged.update(_parse_env(path))
    return merged


_ENV_CACHE: dict[str, str] | None = None


def get(key: str, default: str = "") -> str:
    """
    Priority: Keychain → .env file → environment variable → default.
    Local-First: tokens/keys are stored in Keychain; .env is used as fallback only.
    """
    # 1. Keychain
    val = get_secret(key)
    if val:
        return val

    # 2. .env file
    global _ENV_CACHE
    if _ENV_CACHE is None:
        _ENV_CACHE = _load_env_file()
    val = _ENV_CACHE.get(key)
    if val:
        return val

    # 3. Environment variable
    import os
    return os.environ.get(key, default)


def describe_source(key: str) -> dict:
    """키가 어디서 오는지 진단(값은 마스킹). get() 과 같은 우선순위로 판정.

    반환: {"source": "keychain"|"dotenv"|"env"|"none", "masked": "sk-a…", "env_path": <.env 경로 or None>}
    """
    import os

    def _mask(v: str) -> str:
        if not v:
            return ""
        return (v[:4] + "…") if len(v) > 4 else "…"

    val = get_secret(key)
    if val:
        return {"source": "keychain", "masked": _mask(val), "env_path": None}

    for path in _env_file_paths():
        env = _parse_env(path)
        if env.get(key):
            return {"source": "dotenv", "masked": _mask(env[key]), "env_path": str(path)}

    val = os.environ.get(key, "")
    if val:
        return {"source": "env", "masked": _mask(val), "env_path": None}

    return {"source": "none", "masked": "", "env_path": None}


def migrate_env_to_keychain(keys: list[str] | None = None) -> dict[str, bool]:
    """
    Migrate secrets from .env file to Keychain.
    If keys is None, migrates all entries. Returns: {key: success}
    """
    env = _load_env_file()
    targets = keys if keys is not None else list(env.keys())
    results: dict[str, bool] = {}
    for k in targets:
        v = env.get(k, "")
        if v:
            results[k] = set_secret(k, v)
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "get" and len(sys.argv) > 2:
        val = get(sys.argv[2])
        print(val or "(없음)")
    elif cmd == "set" and len(sys.argv) > 3:
        ok = set_secret(sys.argv[2], sys.argv[3])
        print("저장됨" if ok else "실패")
    elif cmd == "delete" and len(sys.argv) > 2:
        ok = delete_secret(sys.argv[2])
        print("삭제됨" if ok else "없음")
    elif cmd == "migrate":
        keys_arg = sys.argv[2:] if len(sys.argv) > 2 else None
        results = migrate_env_to_keychain(keys_arg)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("사용법: python keychain.py get|set|delete|migrate [key] [value]")
        print("  migrate [KEY1 KEY2 …] — .env → Keychain 이관")
