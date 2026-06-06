# Created: 2026-06-06
# Purpose: 자율 작동 인프라 검증 — YOLO 전역 영속화(재시작 견딤), 자율 추적 세션
#          등록/해제, DB 기반 부활 판정(완료 감지·누적 상한). 유저 없이 장기 멀티턴이
#          서버 재시작에도 이어지도록 하는 핵심 로직.
# Dependencies: web.server
# Test Status: 검증 중

from __future__ import annotations

import importlib

import pytest

server = importlib.import_module("web.server")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # 영속 파일을 임시 경로로 — 실제 data_dir 오염 방지
    monkeypatch.setattr(server, "_yolo_flag_path", lambda: tmp_path / "yolo_global.flag")
    monkeypatch.setattr(server, "_autopilot_path", lambda: tmp_path / "autopilot.json")
    server._YOLO_GLOBAL = False
    server._YOLO_MODE.clear()
    yield
    server._YOLO_GLOBAL = False
    server._YOLO_MODE.clear()


# ── YOLO 전역 영속화 ──

def test_yolo_global_persists_to_disk():
    server._save_yolo_global(True)
    assert server._load_yolo_global() is True
    server._save_yolo_global(False)
    assert server._load_yolo_global() is False


def test_yolo_global_survives_reload():
    """파일이 있으면 모듈 재로드 시 _load가 복원 — 재시작 시뮬레이션."""
    server._save_yolo_global(True)
    # 새 프로세스라면 _YOLO_GLOBAL = _load_yolo_global() 로 복원됨
    assert server._load_yolo_global() is True


# ── 자율 추적 세션 등록/해제 ──

def test_autopilot_register_unregister():
    sid = "auto-1"
    server._autopilot_register(sid)
    assert sid in server._load_autopilot()
    server._autopilot_unregister(sid)
    assert sid not in server._load_autopilot()


def test_autopilot_register_idempotent():
    sid = "auto-2"
    server._autopilot_register(sid)
    server._autopilot_register(sid)  # 두 번 등록해도 resumes 리셋 안 됨
    data = server._load_autopilot()
    assert data[sid]["resumes"] == 0


# ── DB 부활 판정 ──

def test_autopilot_done_detection(monkeypatch):
    """마지막 assistant 메시지가 완료를 명시하면 done으로 본다."""
    def fake_hist(sid):
        return [{"role": "user", "content": "해줘"},
                {"role": "assistant", "content": "모든 작업 완료. 산출물 저장하고 검증까지 끝냈다."}]
    import pipeline.session_store as ss
    monkeypatch.setattr(ss, "load_history", fake_hist)
    assert server._autopilot_looks_done("x") is True


def test_autopilot_not_done(monkeypatch):
    """완료 신호가 없으면 계속 진행 대상."""
    def fake_hist(sid):
        return [{"role": "assistant", "content": "다음 chunk 실행 중… 계속 진행할게."}]
    import pipeline.session_store as ss
    monkeypatch.setattr(ss, "load_history", fake_hist)
    assert server._autopilot_looks_done("x") is False


def test_autopilot_partial_done_not_finished(monkeypatch):
    """회귀: 부분 완료('chunk01/02 완료, 나머지 남음')를 전체 완료로 오판하지 않는다.
    실제로 이 오판 때문에 자율 세션이 60% 남았는데 추적 해제됐다."""
    def fake_hist(sid):
        return [{"role": "assistant",
                 "content": "chunk01, chunk02는 manifest까지 완료. 남은 건 chunk03~09. 이어서 진행할게."}]
    import pipeline.session_store as ss
    monkeypatch.setattr(ss, "load_history", fake_hist)
    assert server._autopilot_looks_done("x") is False


def test_autopilot_done_with_progress_marker_not_finished(monkeypatch):
    """전체 완료 신호가 있어도 진행 신호가 섞이면 미완료."""
    def fake_hist(sid):
        return [{"role": "assistant", "content": "전부 완료했지만 아직 검증이 남았어."}]
    import pipeline.session_store as ss
    monkeypatch.setattr(ss, "load_history", fake_hist)
    assert server._autopilot_looks_done("x") is False


@pytest.mark.asyncio
async def test_db_resume_stops_at_cap(monkeypatch):
    """누적 재개 상한에 도달하면 부활하지 않고 추적 해제."""
    sid = "auto-cap"
    server._autopilot_register(sid)
    data = server._load_autopilot()
    data[sid]["resumes"] = server.HEARTBEAT_DB_MAX_RESUMES
    server._save_autopilot(data)
    # _resume_stalled_session이 안 불리도록 모킹(불리면 실패)
    called = {"n": 0}
    def _boom(s):
        called["n"] += 1
    monkeypatch.setattr(server, "_resume_stalled_session", _boom)
    monkeypatch.setattr(server, "_autopilot_looks_done", lambda s: False)
    result = server._resume_autopilot_db(sid)
    assert result is False
    assert called["n"] == 0                       # 부활 안 함
    assert sid not in server._load_autopilot()    # 추적 해제됨


@pytest.mark.asyncio
async def test_db_resume_stops_when_done(monkeypatch):
    """완료 감지되면 부활하지 않고 추적 해제."""
    sid = "auto-done"
    server._autopilot_register(sid)
    monkeypatch.setattr(server, "_autopilot_looks_done", lambda s: True)
    called = {"n": 0}
    monkeypatch.setattr(server, "_resume_stalled_session", lambda s: called.__setitem__("n", called["n"] + 1))
    result = server._resume_autopilot_db(sid)
    assert result is False
    assert called["n"] == 0
    assert sid not in server._load_autopilot()


@pytest.mark.asyncio
async def test_db_resume_revives(monkeypatch):
    """정상 케이스 — 미완료 + 상한 미만이면 부활하고 카운트 증가."""
    sid = "auto-go"
    server._autopilot_register(sid)
    monkeypatch.setattr(server, "_autopilot_looks_done", lambda s: False)
    called = {"n": 0}
    monkeypatch.setattr(server, "_resume_stalled_session", lambda s: called.__setitem__("n", called["n"] + 1))
    result = server._resume_autopilot_db(sid)
    assert result is True
    assert called["n"] == 1
    assert server._load_autopilot()[sid]["resumes"] == 1
