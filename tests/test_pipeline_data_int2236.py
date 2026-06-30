# Created: 2026-07-01
# Purpose: pipeline 데이터 무결성 회귀 (INT-2236 audit) — graph source·contact memo·YAML.

from __future__ import annotations

import yaml


def test_graph_loads_message_and_event_sources(monkeypatch):
    # message 가 있어도 event source 가 누락되지 않아야 한다 (INT-2236).
    import pipeline.graph_cooccurrence as g
    monkeypatch.setattr(g, "_iter_message_sources", lambda c: ["m1", "m2"])
    monkeypatch.setattr(g, "_iter_event_sources", lambda c: ["e1"])
    assert g._load_sources(None) == ["m1", "m2", "e1"]


def test_graph_event_only_when_no_messages(monkeypatch):
    import pipeline.graph_cooccurrence as g
    monkeypatch.setattr(g, "_iter_message_sources", lambda c: [])
    monkeypatch.setattr(g, "_iter_event_sources", lambda c: ["e1"])
    assert g._load_sources(None) == ["e1"]


def test_update_memo_rejects_empty_name():
    # 빈/공백 이름은 fallback LIKE '%' 로 전체 덮어쓰기가 되므로 거부 (INT-2236).
    from pipeline.contact_store import update_memo
    assert update_memo("", "m") is False
    assert update_memo("   ", "m") is False


def test_command_frontmatter_yaml_safe_roundtrip():
    # description 에 콜론/해시/quote/newline 이 들어가도 safe_dump→safe_load 가 보존돼야
    # frontmatter 가 깨지지 않는다 (INT-2236).
    fm = {"name": "t", "description": 'a: b # c "q"\nsecond line', "argument-hint": "<x>"}
    out = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False)
    parsed = yaml.safe_load(out)
    assert parsed["description"] == 'a: b # c "q"\nsecond line'
    assert parsed["argument-hint"] == "<x>"
