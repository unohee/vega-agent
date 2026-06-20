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


@pytest.mark.asyncio
async def test_entity_resolution_report_metrics(tmp_path, monkeypatch):
    db_path = tmp_path / "agent.db"
    monkeypatch.setattr(mi, "_db", lambda: db_path)

    with mi._conn() as conn:
        entities = [
            ("Acme", "org", "canon:acme"),
            ("ACME", "org", "canon:acme"),
            ("Solo", "person", " canon:solo "),
            ("Noisy", "topic", None),
            ("Blank Canon", "topic", "   "),
            ("Rare", "topic", None),
        ]
        conn.executemany(
            "INSERT INTO entities(name, kind, canonical_id) VALUES (?, ?, ?)",
            entities,
        )
        conn.executemany(
            "INSERT INTO events(event_date, title, body) VALUES (?, ?, '')",
            [("2026-01-01", "e1"), ("2026-01-02", "e2"), ("2026-01-03", "e3")],
        )
        conn.executemany(
            "INSERT INTO event_entities(event_id, entity_id, match_text) VALUES (?, ?, ?)",
            [
                (1, 4, "Noisy"),
                (1, 4, "Noisy"),
                (2, 4, "Noisy"),
                (3, 4, "Noisy"),
                (1, 5, "Blank Canon"),
                (2, 5, "Blank Canon"),
                (3, 6, "Rare"),
            ],
        )
        conn.commit()

    report = _body(await mi.entity_resolution_report())
    assert report["baseline_coverage_percent"] == 3.4
    assert report["current_coverage_percent"] == 50.0
    assert report["post_run_coverage_percent"] == 50.0
    assert report["total_entities"] == 6
    assert report["canonicalized_entities"] == 3

    duplicates = report["duplicate_canonical_clusters"]
    assert duplicates["total"] == 1
    assert duplicates["clusters"] == [
        {
            "canonical_id": "canon:acme",
            "count": 2,
            "entities": [
                {"id": 2, "name": "ACME", "kind": "org"},
                {"id": 1, "name": "Acme", "kind": "org"},
            ],
        }
    ]

    unresolved = report["unresolved_high_frequency_entities"]
    assert unresolved[0] == {
        "id": 4,
        "name": "Noisy",
        "kind": "topic",
        "event_count": 3,
        "mention_count": 4,
    }
    assert [row["name"] for row in unresolved] == ["Noisy", "Blank Canon", "Rare"]

    summary = _body(await mi.memory_summary())
    assert summary["entity_resolution"]["baseline_coverage_percent"] == 3.4
    assert summary["entities"]["entity_resolution"]["current_coverage_percent"] == 50.0


# ── persona: 페르소나 탭 ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_persona_active_only():
    resp = await mi.list_persona(active_only=True, search="")
    d = _body(resp)
    assert "rows" in d
    for r in d["rows"]:
        assert r["is_active"] == 1
        assert "section_key" in r and "content" in r


# ── memory settings: Memory & Context 패널 (INT-1473 버그2 회귀) ─────────────
class TestMemorySettingsAPI:
    """GET/POST /api/memory/settings — settings.html Memory&Context 패널 계약."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pipeline.data_paths.memory_settings_path",
            lambda: tmp_path / "memory_settings.json",
        )
        fastapi = pytest.importorskip("fastapi")
        testclient = pytest.importorskip("fastapi.testclient")
        app = fastapi.FastAPI()
        app.include_router(mi.router)
        return testclient.TestClient(app)

    def test_get_returns_settings_and_defaults(self, client):
        r = client.get("/api/memory/settings")
        assert r.status_code == 200
        body = r.json()
        # settings.html Memory & Context 패널이 읽는 키
        s = body["settings"]
        assert "compact_threshold" in s and "keep_recent" in s and "auto_memory_update" in s
        assert "defaults" in body

    def test_post_saves_and_returns_saved(self, client):
        r = client.post("/api/memory/settings",
                        json={"compact_threshold": 30, "keep_recent": 8,
                              "auto_memory_update": False})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["settings"]["compact_threshold"] == 30
        assert body["settings"]["keep_recent"] == 8
        assert body["settings"]["auto_memory_update"] is False
        # GET 재조회로 영속 확인
        again = client.get("/api/memory/settings").json()["settings"]
        assert again["compact_threshold"] == 30

    def test_post_clamps_validation_rules(self, client):
        """검증 규칙: threshold>=4, keep>=2, keep<threshold (서버가 보정)."""
        r = client.post("/api/memory/settings",
                        json={"compact_threshold": 1, "keep_recent": 99})
        assert r.status_code == 200
        s = r.json()["settings"]
        assert s["compact_threshold"] >= 4
        assert s["keep_recent"] >= 2
        assert s["keep_recent"] < s["compact_threshold"]

    def test_post_invalid_value_400(self, client):
        r = client.post("/api/memory/settings", json={"compact_threshold": "abc"})
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_saved_settings_drive_compaction(self, client):
        """저장된 설정이 실제 compaction 트리거에 반영되는지 — 죽은 설정 방지."""
        from pipeline.compaction import _needs_compaction
        client.post("/api/memory/settings", json={"compact_threshold": 6, "keep_recent": 2})
        history_5 = [{"role": "user", "content": str(i)} for i in range(5)]
        history_6 = [{"role": "user", "content": str(i)} for i in range(6)]
        assert _needs_compaction(history_5) is False
        assert _needs_compaction(history_6) is True
