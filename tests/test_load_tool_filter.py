# Created: 2026-06-24
# Purpose: light load tool schema filter (INT-1893 Phase 2).

from __future__ import annotations

from pipeline.tools import TOOL_SCHEMAS, get_schemas_for_mode


def test_light_load_excludes_bash_exec():
    all_names = {s.get("name") for s in get_schemas_for_mode(TOOL_SCHEMAS)}
    light_names = {s.get("name") for s in get_schemas_for_mode(TOOL_SCHEMAS, load="light")}
    assert "bash_exec" in all_names or "host_exec" in all_names
    assert "bash_exec" not in light_names
    assert "host_exec" not in light_names
    assert "web_search" in light_names


def test_standard_load_keeps_full_schemas():
    std = get_schemas_for_mode(TOOL_SCHEMAS, load="standard")
    base = get_schemas_for_mode(TOOL_SCHEMAS)
    assert len(std) == len(base)
