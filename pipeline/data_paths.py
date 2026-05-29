# Created: 2026-05-27
# Purpose: VEGA data directory abstraction. All DB/config/persona paths are resolved here.
# Dependencies: stdlib

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REPO_DATA = _REPO_ROOT / "data"  # Code-level data bundled with the repo (commands/, agents/, mcp.json, etc.)


@lru_cache(maxsize=1)
def data_dir() -> Path:
    """User data root. Resolution order:
    1. VEGA_DATA_DIR environment variable
    2. macOS: ~/Library/Application Support/VEGA
    3. Linux/other: ~/.local/share/VEGA
    Created automatically if it does not exist.
    """
    env = os.environ.get("VEGA_DATA_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
    elif os.name == "posix" and Path("/Library").exists():
        p = Path.home() / "Library" / "Application Support" / "VEGA"
    else:
        p = Path.home() / ".local" / "share" / "VEGA"
    p.mkdir(parents=True, exist_ok=True)
    return p


def repo_data_dir() -> Path:
    """Repo-committed code-level data (commands/, agents/, mcp.json, etc.) — do NOT confuse with user data root."""
    return _REPO_DATA


def db_path() -> Path:
    return data_dir() / "vega.db"


def contacts_db_path() -> Path:
    return data_dir() / "contacts.db"


def user_profile_path() -> Path:
    return data_dir() / "user_profile.json"


def persona_path() -> Path:
    """Current user's persona markdown — resolved under the user data root."""
    return data_dir() / "persona.md"


def widgets_path() -> Path:
    return data_dir() / "widgets.json"


def llm_providers_path() -> Path:
    return data_dir() / "llm_providers.json"


def mcp_config_path() -> Path:
    return data_dir() / "mcp.json"


def tool_groups_path() -> Path:
    return data_dir() / "tool_groups.json"


def uploads_dir() -> Path:
    p = data_dir() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def charts_dir() -> Path:
    p = data_dir() / "charts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def commands_dir() -> Path:
    """Slash command markdowns — searched in both repo and user dirs.
    Repo commands/ provides defaults; user commands/ adds or overrides them."""
    return _REPO_DATA / "commands"


def user_commands_dir() -> Path:
    p = data_dir() / "commands"
    p.mkdir(parents=True, exist_ok=True)
    return p


def agents_dir() -> Path:
    """Per-provider agent.md files (bundled with repo)."""
    return _REPO_DATA / "agents"


def is_first_run() -> bool:
    """Returns True if user_profile.json is absent from the user data root (first launch)."""
    return not user_profile_path().exists()
