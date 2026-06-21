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
    3. Windows: %LOCALAPPDATA%\\VEGA  (Rust 셸 log_dir와 같은 루트 — dirs_next::data_local_dir)
    4. Linux/other: ~/.local/share/VEGA
    Created automatically if it does not exist.
    """
    env = os.environ.get("VEGA_DATA_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
    elif os.name == "posix" and Path("/Library").exists():
        p = Path.home() / "Library" / "Application Support" / "VEGA"
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        p = base / "VEGA"
    else:
        p = Path.home() / ".local" / "share" / "VEGA"
    p.mkdir(parents=True, exist_ok=True)
    return p


def repo_data_dir() -> Path:
    """Repo-committed code-level data (commands/, agents/, mcp.json, etc.) — do NOT confuse with user data root."""
    return _REPO_DATA


@lru_cache(maxsize=1)
def log_dir() -> Path:
    """User log root. Resolution order:
    1. VEGA_LOG_DIR environment variable
    2. macOS: ~/Library/Logs/VEGA
    3. Linux/other: <data_dir>/logs
    Created automatically if it does not exist.

    NOTE: 로그는 .app 번들 안에 두지 않는다 — 코드서명 검증/자동업데이트가
    번들 내부 쓰기를 깨고 재설치 시 사라지기 때문. macOS 표준 사용자 로그 위치를 쓴다.
    """
    env = os.environ.get("VEGA_LOG_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
    elif os.name == "posix" and Path("/Library").exists():
        p = Path.home() / "Library" / "Logs" / "VEGA"
    else:
        p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    # Canonical VEGA DB path.
    # 절대경로면 그대로, 파일명만 주면 data_dir() 하위로 해석.
    override = os.environ.get("VEGA_DB_FILE", "").strip()
    if override:
        p = Path(override).expanduser()
        return p if p.is_absolute() else data_dir() / p
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


def memory_settings_path() -> Path:
    """메모리·컨텍스트 설정(JSON) — compaction 임계값 등. 사용자 데이터 root 기준."""
    return data_dir() / "memory_settings.json"


def settings_path() -> Path:
    """런타임 설정(settings.json) — SearXNG URL 등 온보딩·설정창에서 변경하는 값."""
    return data_dir() / "settings.json"


def access_policy_path() -> Path:
    """파일 접근 정책(access_policy.json) — 사용자가 설정창에서 조정하는
    allowlist/denylist. 하드코딩 기본값(path_guard) 위에 얹는 사용자 레이어."""
    return data_dir() / "access_policy.json"


def llm_providers_path() -> Path:
    return data_dir() / "llm_providers.json"


def mcp_config_path() -> Path:
    return data_dir() / "mcp.json"


def tool_groups_path() -> Path:
    return data_dir() / "tool_groups.json"


def slack_oauth_client_path() -> Path:
    """Slack OAuth client config. User data overrides the bundled repo default."""
    user_path = data_dir() / "slack_oauth_client.json"
    if user_path.exists():
        return user_path
    return _REPO_DATA / "slack_oauth_client.json"


def google_oauth_client_path() -> Path:
    """Google OAuth client config (내장 Desktop 앱 클라이언트). User data overrides repo default."""
    user_path = data_dir() / "google_oauth_client.json"
    if user_path.exists():
        return user_path
    return _REPO_DATA / "google_oauth_client.json"


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


def agent_md_path(name: str) -> Path:
    """Agent guide markdown 읽기 경로. 사용자가 편집한 런타임본(data_dir/agents)이
    존재하고 비어있지 않으면 그것을, 아니면 레포 번들 기본본(_REPO_DATA/agents)을 반환.
    slack/google OAuth override 패턴과 동일. 0바이트 런타임 파일은 레포 번들본으로
    폴백한다 (시드 누락·빈 파일 사고 방어 — INT-1587). 쓰기는 항상 data_dir/agents."""
    user_path = data_dir() / "agents" / f"{name}.md"
    try:
        if user_path.exists() and user_path.stat().st_size > 0:
            return user_path
    except OSError:
        pass
    return _REPO_DATA / "agents" / f"{name}.md"


def is_first_run() -> bool:
    """Returns True if user_profile.json is absent from the user data root (first launch)."""
    return not user_profile_path().exists()
