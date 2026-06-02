# Created: 2026-06-03
# Purpose: Memory Inspector API 회귀 테스트 — 메모리 생태계 대시보드가 의존하는 엔드포인트
# Dependencies: web/routers/memory_inspector.py
# Test Status: 검증 중

from __future__ import annotations

import json

import pytest

from web.routers import memory_inspector as mi


def _body(resp):
    """JSONResponse → dict."""
    return json.loads(resp.body)


# ── summary: 대시보드 히어로 통계가 의존 ──────────────────────────
@pytest.mark.asyncio
async def test_summary_shape():
    resp = await mi.memory_summary()
    d = _body(resp)
    # 대시보드 hero가 읽는 키 — 하나라도 빠지면 통계 '—'로 깨짐
    assert "persona" in d and "total" in d["persona"] and "active" in d["persona"]
    assert "events" in d and "total" in d["events"]
    assert "entities" in d and "total" in d["entities"] and "by_kind" in d["entities"]
    assert "sessions" in d and "total" in d["sessions"]  # 신규 추가 키
    assert isinstance(d["entities"]["by_kind"], list)


# ── rules: 신규 엔드포인트 (규칙·스킬 탭) ─────────────────────────
@pytest.mark.asyncio
async def test_rules_shape():
    resp = await mi.list_rules()
    d = _body(resp)
    assert "rules" in d
    assert isinstance(d["rules"], list)
    # 규칙이 있으면 프론트가 읽는 키 확인
    for r in d["rules"]:
        assert "rule_id" in r and "section" in r and "rule_text" in r


# ── entities: 인물·엔티티 탭 ──────────────────────────────────────
@pytest.mark.asyncio
async def test_entities_list_shape():
    resp = await mi.list_entities(kind="", search="", limit=5, offset=0)
    d = _body(resp)
    assert "rows" in d and "total" in d
    assert isinstance(d["rows"], list)
    for r in d["rows"]:
        # 프론트 renderEntities가 읽는 키
        assert "id" in r and "kind" in r and "name" in r
        assert "aliases_json" in r  # JSON.parse 대상


@pytest.mark.asyncio
async def test_entities_kind_filter():
    """kind 필터가 실제로 좁혀지는지 (전체 >= 필터 결과)."""
    all_resp = _body(await mi.list_entities(kind="", search="", limit=5, offset=0))
    person_resp = _body(await mi.list_entities(kind="person", search="", limit=5, offset=0))
    # person만 반환되는지
    for r in person_resp["rows"]:
        assert r["kind"] == "person"
    assert person_resp["total"] <= all_resp["total"]


# ── events: 타임라인 탭 ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_events_list_shape():
    resp = await mi.list_events(search="", tag="", limit=5, offset=0)
    d = _body(resp)
    assert "rows" in d and "total" in d and "offset" in d
    for r in d["rows"]:
        assert "id" in r and "event_date" in r and "title" in r


# ── persona: 페르소나 탭 ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_persona_active_only():
    resp = await mi.list_persona(active_only=True, search="")
    d = _body(resp)
    assert "rows" in d
    for r in d["rows"]:
        assert r["is_active"] == 1
        assert "section_key" in r and "content" in r
