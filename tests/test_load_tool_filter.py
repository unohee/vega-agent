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


def test_light_load_allows_dispatch_agent_for_parallel_search():
    # 검색 등 병렬 호출이 필요한 단순 작업에서 sub-agent fan-out 가능해야 한다.
    light_names = {s.get("name") for s in get_schemas_for_mode(TOOL_SCHEMAS, load="light")}
    assert "dispatch_agent" in light_names
    # exec/office write 는 여전히 light 에서 제외
    assert "python_exec" not in light_names
