# Created: 2026-06-08
# Purpose: pipeline/cron_jobs.py + cron API — 임의 프롬프트 예약 (INT-1407)
# Dependencies: pytest, croniter, fastapi TestClient

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeline import cron_jobs as cj


@pytest.fixture
def tmp_cron(tmp_path, monkeypatch):
    monkeypatch.setattr(cj, "_path", lambda: tmp_path / "cron_jobs.json")
    return tmp_path


class TestCreate:
    def test_create_valid(self, tmp_cron):
        job = cj.create_job("아침 브리핑 요약", "0 9 * * *", "morning")
        assert job.get("id")
        assert job["schedule"] == "0 9 * * *"
        assert job["enabled"] is True
        assert job["next_run"]  # 다음 실행 시각 계산됨

    def test_create_invalid_cron(self, tmp_cron):
        res = cj.create_job("x", "not a cron")
        assert res.get("error")

    def test_create_empty_prompt(self, tmp_cron):
        res = cj.create_job("", "0 9 * * *")
        assert res.get("error")

    def test_label_defaults_to_prompt(self, tmp_cron):
        job = cj.create_job("이 프롬프트가 라벨이 된다", "0 9 * * *")
        assert "이 프롬프트" in job["label"]


class TestListDelete:
    def test_list_persists(self, tmp_cron):
        cj.create_job("a", "0 9 * * *")
        cj.create_job("b", "0 10 * * *")
        assert len(cj.list_jobs()) == 2

    def test_delete(self, tmp_cron):
        j = cj.create_job("a", "0 9 * * *")
        assert cj.delete_job(j["id"]).get("ok")
        assert len(cj.list_jobs()) == 0

    def test_delete_missing(self, tmp_cron):
        assert cj.delete_job("nope").get("error")

    def test_toggle(self, tmp_cron):
        j = cj.create_job("a", "0 9 * * *")
        cj.set_enabled(j["id"], False)
        assert cj.list_jobs()[0]["enabled"] is False


class TestDueAndMark:
    def test_due_when_past(self, tmp_cron, monkeypatch):
        # 과거 시각으로 생성 → 즉시 due
        base = datetime.fromisoformat("2026-06-08T08:00:00+09:00")
        j = cj.create_job("x", "0 9 * * *", now_iso="2026-06-07T00:00:00+09:00")
        # next_run은 2026-06-07 09:00 → 2026-06-08 10:00 기준이면 due
        now = datetime.fromisoformat("2026-06-08T10:00:00+09:00")
        due = cj.due_jobs(now=now)
        assert any(d["id"] == j["id"] for d in due)

    def test_disabled_not_due(self, tmp_cron):
        j = cj.create_job("x", "0 9 * * *", now_iso="2026-06-07T00:00:00+09:00")
        cj.set_enabled(j["id"], False)
        now = datetime.fromisoformat("2026-06-08T10:00:00+09:00")
        assert not any(d["id"] == j["id"] for d in cj.due_jobs(now=now))

    def test_mark_run_advances_next(self, tmp_cron):
        j = cj.create_job("x", "0 9 * * *", now_iso="2026-06-07T00:00:00+09:00")
        before = cj.list_jobs()[0]["next_run"]
        cj.mark_run(j["id"], "ok", now=datetime.fromisoformat("2026-06-08T10:00:00+09:00"))
        after = cj.list_jobs()[0]
        assert after["last_status"] == "ok"
        assert after["next_run"] != before  # 재계산됨


class TestCronAPI:
    @pytest.fixture
    def client(self, tmp_cron):
        import importlib
        import web.routers.cron as cron_router
        importlib.reload(cron_router)
        app = FastAPI()
        app.include_router(cron_router.router)
        return TestClient(app)

    def test_post_and_list(self, client):
        r = client.post("/api/cron", json={"prompt": "테스트", "schedule": "0 9 * * *"}).json()
        assert r["ok"] is True
        jobs = client.get("/api/cron").json()["jobs"]
        assert len(jobs) == 1

    def test_post_invalid(self, client):
        r = client.post("/api/cron", json={"prompt": "x", "schedule": "bad"})
        assert r.status_code == 400

    def test_delete(self, client):
        jid = client.post("/api/cron", json={"prompt": "x", "schedule": "0 9 * * *"}).json()["job"]["id"]
        assert client.request("DELETE", f"/api/cron/{jid}").json()["ok"]
