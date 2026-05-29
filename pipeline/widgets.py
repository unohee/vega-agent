# Created: 2026-05-25
# Purpose: Agent View custom widget storage/management — called by /widget wizard via widget_save
# Dependencies: stdlib

from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.data_paths import widgets_path as _widgets_path
WIDGETS_PATH = _widgets_path()

# Kept in sync with server whitelist — data sources VEGA is allowed to use
VALID_SOURCES = {
    "clock", "session_count", "recent_command",
    "mail_count", "today_brief", "calendar_today", "project_count", "skill_count",
}
VALID_TYPES = {"stat", "list", "text", "action"}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SKILL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_INPUT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VALID_INPUT_TYPES = {"text", "url", "number", "textarea"}


def _load() -> dict:
    if not WIDGETS_PATH.exists():
        return {"widgets": []}
    try:
        data = json.loads(WIDGETS_PATH.read_text(encoding="utf-8"))
        if "widgets" not in data:
            data["widgets"] = []
        return data
    except Exception:
        return {"widgets": []}


def _save(data: dict) -> None:
    WIDGETS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_widget(widget_id: str, title: str, type: str, source: str = "",
                icon: str = "🧩", span: int = 1, text: str = "",
                inputs: list | None = None, skill: str = "",
                overwrite: bool = False) -> dict:
    """Add or update a widget in Agent View. Called by /widget wizard after confirmation.
    type='action' requires inputs (form fields) and skill (slash command name).
    Returns: {ok, id|error}."""
    widget_id = (widget_id or "").strip().lower()
    if not _ID_RE.match(widget_id):
        return {"ok": False, "error": f"잘못된 id: '{widget_id}' (소문자/숫자/하이픈)"}
    if type not in VALID_TYPES:
        return {"ok": False, "error": f"type은 {sorted(VALID_TYPES)} 중 하나"}
    if source and source not in VALID_SOURCES:
        return {"ok": False, "error": f"source '{source}'는 화이트리스트에 없음. 가능: {sorted(VALID_SOURCES)}"}
    if type == "text" and not source and not text.strip():
        return {"ok": False, "error": "text 위젯은 source 또는 text 중 하나 필요"}

    norm_inputs: list[dict] = []
    if type == "action":
        skill = (skill or "").strip().lstrip("/").lower()
        if not _SKILL_RE.match(skill):
            return {"ok": False, "error": "action 위젯은 skill(슬래시 커맨드 이름) 필수 — 예: 'youtube-meta'"}
        from pipeline.commands import get_command
        if get_command(skill) is None:
            return {"ok": False, "error": f"슬래시 커맨드 '/{skill}' 없음 — skill_save로 먼저 등록해라"}
        if not isinstance(inputs, list) or not inputs:
            return {"ok": False, "error": "action 위젯은 inputs 배열이 1개 이상 필요"}
        seen_names: set[str] = set()
        for inp in inputs[:8]:
            if not isinstance(inp, dict):
                return {"ok": False, "error": "각 input은 객체여야 함"}
            name = (inp.get("name") or "").strip().lower()
            if not _INPUT_NAME_RE.match(name):
                return {"ok": False, "error": f"input name '{name}' 불가 (영문 소문자 시작, [a-z0-9_])"}
            if name in seen_names:
                return {"ok": False, "error": f"input name '{name}' 중복"}
            seen_names.add(name)
            itype = (inp.get("type") or "text").strip().lower()
            if itype not in VALID_INPUT_TYPES:
                return {"ok": False, "error": f"input type '{itype}' 불가 — 가능: {sorted(VALID_INPUT_TYPES)}"}
            norm_inputs.append({
                "name": name,
                "label": (inp.get("label") or name).strip(),
                "type": itype,
                "placeholder": (inp.get("placeholder") or "").strip(),
                "required": bool(inp.get("required", True)),
            })

    try:
        span = int(span)
    except Exception:
        span = 1
    span = max(1, min(3, span))

    data = _load()
    widgets = data["widgets"]
    existing = next((i for i, w in enumerate(widgets) if w.get("id") == widget_id), None)
    if existing is not None and not overwrite:
        return {"ok": False, "error": f"위젯 '{widget_id}' 이미 존재. overwrite=true로 수정"}

    widget: dict = {"id": widget_id, "title": title.strip() or widget_id,
                    "icon": icon or "🧩", "type": type, "span": span}
    if source:
        widget["source"] = source
    if text.strip():
        widget["text"] = text.strip()
    if type == "action":
        widget["skill"] = skill
        widget["inputs"] = norm_inputs

    if existing is not None:
        widgets[existing] = widget
    else:
        widgets.append(widget)
    _save(data)
    return {"ok": True, "id": widget_id, "count": len(widgets)}


def delete_widget(widget_id: str) -> dict:
    widget_id = (widget_id or "").strip().lower()
    data = _load()
    before = len(data["widgets"])
    data["widgets"] = [w for w in data["widgets"] if w.get("id") != widget_id]
    if len(data["widgets"]) == before:
        return {"ok": False, "error": f"위젯 '{widget_id}' 없음"}
    _save(data)
    return {"ok": True, "id": widget_id}


def list_widget_ids() -> list[str]:
    return [w.get("id", "") for w in _load()["widgets"]]
