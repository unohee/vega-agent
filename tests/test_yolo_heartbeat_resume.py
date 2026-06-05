# Created: 2026-06-06
# Purpose: YOLO 하트비트 재개(_resume_stalled_session)의 실제 동작 검증 —
#          재개 turn 추가, 재개 카운트 증가, reg 교체, 중복 재개 방지.
#          _run_gpt_task는 모킹(실제 LLM 호출 없이 태스크 생성만 확인).
# Dependencies: web.server
# Test Status: 검증 중

from __future__ import annotations

import asyncio
import importlib

import pytest

server = importlib.import_module("web.server")

SID = "hb-resume-sid"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # _run_gpt_task를 즉시 끝나는 코루틴으로 대체 — 실제 GPT 호출 차단
    async def _fake_run(sid, history, images):
        return None
    monkeypatch.setattr(server, "_run_gpt_task", _fake_run)
    # 히스토리 로더도 메모리만 쓰도록
    server._SESSION_HISTORY[SID] = [{"role": "user", "content": "원래 목표"},
                                    {"role": "assistant", "content": "진행 중…"}]
    server._YOLO_MODE[SID] = True
    server._heartbeat_resumes.pop(SID, None)
    server._TASK_REGISTRY.pop(SID, None)
    yield
    for d in (server._SESSION_HISTORY, server._YOLO_MODE, server._heartbeat_resumes, server._TASK_REGISTRY):
        d.pop(SID, None)


@pytest.mark.asyncio
async def test_resume_adds_turn_and_counts():
    server._resume_stalled_session(SID)
    hist = server._SESSION_HISTORY[SID]
    # 재개 지시 user turn이 끝에 추가됨
    assert hist[-1]["role"] == "user"
    assert "이어서 계속" in hist[-1]["content"]
    # 재개 카운트 1
    assert server._heartbeat_resumes[SID] == 1
    # 새 reg로 교체됨 (done=False, 하트비트 표식)
    reg = server._TASK_REGISTRY[SID]
    assert reg["done"] is False
    assert reg.get("_heartbeat_resumed") is True
    assert reg["task"] is not None
    # 생성된 태스크 정리
    reg["task"].cancel()
    try:
        await reg["task"]
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_resume_no_duplicate_turn():
    """연속 재개 시 같은 재개 지시를 중복으로 쌓지 않는다."""
    server._resume_stalled_session(SID)
    t1 = server._TASK_REGISTRY[SID]["task"]
    len_after_first = len(server._SESSION_HISTORY[SID])
    server._resume_stalled_session(SID)
    t2 = server._TASK_REGISTRY[SID]["task"]
    len_after_second = len(server._SESSION_HISTORY[SID])
    # 두 번째 재개는 재개 turn을 또 추가하지 않음 (직전이 이미 재개 turn)
    assert len_after_second == len_after_first
    # 카운트는 2로 증가
    assert server._heartbeat_resumes[SID] == 2
    for t in (t1, t2):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_resume_increments_to_cap():
    """재개를 반복하면 카운트가 상한까지 증가 — 이후 _is_stalled_yolo가 막는다."""
    import time
    for _ in range(server.HEARTBEAT_MAX_RESUMES):
        server._resume_stalled_session(SID)
        server._TASK_REGISTRY[SID]["task"].cancel()
        try:
            await server._TASK_REGISTRY[SID]["task"]
        except asyncio.CancelledError:
            pass
    assert server._heartbeat_resumes[SID] == server.HEARTBEAT_MAX_RESUMES
    # 상한 도달 → 더는 멈춤 대상 아님
    stale_reg = {"done": False, "awaiting_approval": False,
                 "last_activity": time.monotonic() - server.HEARTBEAT_STALL_SEC - 60}
    assert server._is_stalled_yolo(SID, stale_reg, time.monotonic()) is False
