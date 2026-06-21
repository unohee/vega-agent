# Created: 2026-06-21
# Purpose: Spawn tree / dispatch_agent focused tests.
# Dependencies: pipeline.spawn, web.routers.spawn

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clear_spawn_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VEGA_DB_FILE", str(tmp_path / "vega.db"))
    from pipeline import data_paths
    from pipeline import spawn

    data_paths.data_dir.cache_clear()
    data_paths.log_dir.cache_clear()
    with spawn._lock:
        spawn._TREES.clear()
    yield
    with spawn._lock:
        spawn._TREES.clear()
    data_paths.data_dir.cache_clear()
    data_paths.log_dir.cache_clear()


def test_dispatch_agent_requires_live_turn_context():
    from pipeline.spawn import dispatch_agent

    result = dispatch_agent(prompt="Inspect the current repository state")
    assert "error" in result
    assert "live VEGA turn" in result["error"]


def test_spawn_tree_api_empty():
    from web.routers import spawn as spawn_router

    app = FastAPI()
    app.include_router(spawn_router.router)
    client = TestClient(app)

    resp = client.get("/api/spawn/tree", params={"session_uuid": "session-a"})
    assert resp.status_code == 200
    assert resp.json() == {"agents": [], "count": 0, "active": 0}


@pytest.mark.asyncio
async def test_dispatch_agent_streams_child_updates(monkeypatch):
    from pipeline import spawn

    async def fake_stream_gpt(
        messages,
        system,
        on_token,
        on_tool_start=None,
        on_tool_done=None,
        on_waiting=None,
        **kwargs,
    ):
        if on_waiting:
            await on_waiting()
        await on_token("child result")
        return "child result"

    monkeypatch.setattr("pipeline.streaming.build_system", lambda working_dir=None: "system")
    monkeypatch.setattr("pipeline.streaming.stream_gpt", fake_stream_gpt)

    loop = asyncio.get_running_loop()
    reg = {
        "buf": [],
        "consumer": asyncio.Event(),
        "last_activity": 0.0,
    }

    def call_dispatch():
        spawn.set_dispatch_context(
            parent_session_id="session-a",
            parent_reg=reg,
            loop=loop,
            working_dir=None,
            plan_mode=False,
            ce_mode=False,
            research_mode=False,
        )
        try:
            return spawn.dispatch_agent(
                prompt="Inspect the current repository state",
                label="repo scan",
                wait_for_result=True,
                timeout_sec=5,
            )
        finally:
            spawn.clear_dispatch_context()

    result = await loop.run_in_executor(None, call_dispatch)

    assert result["ok"] is True
    assert result["status"] == "done"
    assert result["agent"]["result"] == "child result"
    assert spawn.active_count("session-a") == 0
    assert [item["event"] for item in reg["buf"]].count("spawn_update") >= 2
