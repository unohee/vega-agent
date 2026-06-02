# Created: 2026-05-25
# Purpose: VEGA slash command (Skills) dynamic loader — ported from pi/Claude Code pattern
# Dependencies: PyYAML
# Test Status: under validation

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# VEGA-specific commands directory (separate from Claude Code commands)
COMMANDS_DIR = Path(__file__).parent.parent / "data" / "commands"

# Built-in command names — reserved to prevent collisions with dynamic commands (handled directly by handle_slash)
BUILTIN_NAMES = {
    "events", "who", "tag", "search", "context", "persona",
    "sessions", "resume", "new", "rename", "help",
    "plan", "plan-off",
}

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Command:
    name: str
    description: str
    argument_hint: str
    body: str
    path: str


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse --- ... --- YAML frontmatter → (meta, body). Returns ({}, full text) if absent."""
    if not text.startswith("---"):
        return {}, text
    # Find the closing --- after the opening ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    try:
        meta = yaml.safe_load(raw) or {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    return meta, body


def _load_one(path: Path) -> Command | None:
    """Load a single .md file → Command. Name from frontmatter 'name' field, falls back to filename."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    meta, body = _parse_frontmatter(text)
    name = str(meta.get("name") or path.stem).strip().lower()
    if not _NAME_RE.match(name):
        return None  # Invalid name — skip
    desc = str(meta.get("description") or "").strip()
    hint = str(meta.get("argument-hint") or meta.get("argument_hint") or "").strip()
    return Command(name=name, description=desc, argument_hint=hint, body=body.strip(), path=str(path))


def load_commands() -> dict[str, Command]:
    """Load *.md → {name: Command}. Re-scans disk on every call (hot-reload).
    user 디렉터리(영속)를 먼저 스캔해 같은 이름이면 사용자 정의가 번들 기본을 덮어쓴다."""
    out: dict[str, Command] = {}
    dirs = []
    try:
        from pipeline.data_paths import user_commands_dir
        dirs.append(user_commands_dir())   # 영속 user 커맨드 우선
    except Exception:
        pass
    dirs.append(COMMANDS_DIR)              # 번들 기본 커맨드
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            cmd = _load_one(p)
            if cmd and cmd.name not in BUILTIN_NAMES and cmd.name not in out:
                out[cmd.name] = cmd
    return out


def get_command(name: str) -> Command | None:
    """Look up a single command by name (without leading /)."""
    return load_commands().get(name.lstrip("/").lower())


def expand_command(cmd: Command, args: str) -> str:
    """Expands a command body into the message to send to the LLM.
    Substitutes $ARGUMENTS / $@ with args; if absent, appends args after the body."""
    body = cmd.body
    if "$ARGUMENTS" in body or "$@" in body:
        body = body.replace("$ARGUMENTS", args).replace("$@", args)
    elif args:
        body = f"{body}\n\n[추가 인자]\n{args}"
    return (
        f"[슬래시 커맨드 실행: /{cmd.name}]\n"
        f"아래 지시를 네가 가진 도구(bash_exec, file_read, host_exec 등)로 직접 수행해라.\n"
        f"---\n{body}"
    )


def save_command(name: str, description: str, body: str,
                 argument_hint: str = "", overwrite: bool = False) -> dict:
    """Saves a new slash command to data/commands/{name}.md.
    Called by the /skill creation wizard once content is agreed upon.
    Returns: {ok, path|error}."""
    name = (name or "").strip().lower().lstrip("/")
    if not _NAME_RE.match(name):
        return {"ok": False, "error": f"잘못된 이름: '{name}' (소문자/숫자/하이픈만, 예: my-skill)"}
    if name in BUILTIN_NAMES:
        return {"ok": False, "error": f"'{name}'은 내장 커맨드 이름이라 사용 불가"}
    if not (description or "").strip():
        return {"ok": False, "error": "description은 필수"}
    if not (body or "").strip():
        return {"ok": False, "error": "body(본문)는 필수"}

    # 사용자 커맨드는 영속 user 디렉터리에 쓴다 — 번들 내 COMMANDS_DIR 은
    # onefile 에서 읽기전용/임시(_MEIPASS)라 mkdir/write 가 깨진다.
    from pipeline.data_paths import user_commands_dir
    write_dir = user_commands_dir()
    write_dir.mkdir(parents=True, exist_ok=True)
    dest = write_dir / f"{name}.md"
    if dest.exists() and not overwrite:
        return {"ok": False, "error": f"'/{name}' 이미 존재. 덮어쓰려면 overwrite=true"}

    fm_lines = ["---", f"name: {name}", f"description: {description.strip()}"]
    if argument_hint.strip():
        fm_lines.append(f'argument-hint: "{argument_hint.strip()}"')
    fm_lines.append("---")
    content = "\n".join(fm_lines) + "\n\n" + body.strip() + "\n"
    dest.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(dest), "name": name}


def delete_command(name: str) -> dict:
    """Delete a custom command (routed through trash directory)."""
    name = (name or "").strip().lower().lstrip("/")
    dest = COMMANDS_DIR / f"{name}.md"
    if not dest.exists():
        return {"ok": False, "error": f"'/{name}' 없음"}
    trash = COMMANDS_DIR.parent / ".commands_trash"
    trash.mkdir(exist_ok=True)
    dest.rename(trash / f"{name}.md")
    return {"ok": True, "name": name}


def format_commands_for_prompt() -> str:
    """Formats the list of available custom commands for inclusion in the system prompt."""
    cmds = load_commands()
    if not cmds:
        return ""
    lines = ["\n## 사용 가능한 슬래시 커맨드 (사용자가 /이름 으로 호출)"]
    for c in cmds.values():
        hint = f" {c.argument_hint}" if c.argument_hint else ""
        lines.append(f"- `/{c.name}{hint}` — {c.description or '(설명 없음)'}")
    return "\n".join(lines) + "\n"
