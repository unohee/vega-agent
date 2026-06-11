# Created: 2026-06-11
# Purpose: Superthread OAuth 워크스페이스 동적 발견 회귀 테스트 (INT-1451)
#          — PAT 발급이 하드코딩 워크스페이스가 아니라 사용자의 team 으로 가는지.
# Dependencies: pytest, unittest.mock
# Test Status: passing

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.auth import superthread as st


def _prime_pending_state():
    st._pending_state.clear()
    st._pending_state.update({
        "state": "test-state",
        "code_verifier": "test-verifier",
        "redirect_uri": "http://127.0.0.1:8100/callback",
    })


def test_exchange_code_uses_discovered_workspace():
    """토큰 교환 후 users/me 로 발견한 team id 로 PAT 를 발급해야 한다."""
    _prime_pending_state()
    pat_urls = []

    def fake_post_json(url, payload, headers=None):
        pat_urls.append(url)
        return {"token": "pat-xyz", "pat": {"time_expires": 1800000000}}

    with patch.object(st, "_post_form", return_value={"access_token": "at-123"}), \
         patch.object(st, "_get_json", return_value={"user": {"teams": [{"id": "tmUSER42"}]}}), \
         patch.object(st, "_post_json", side_effect=fake_post_json), \
         patch.object(st, "keychain_save") as mock_save:
        result = st.exchange_code("auth-code", state="test-state")

    assert result["ok"] is True, result
    assert pat_urls == [f"{st._API_BASE}/auth/tmUSER42/pats"]
    saved = {c.args[0]: c.args[1] for c in mock_save.call_args_list}
    assert saved["pat"] == "pat-xyz"
    assert saved["workspace_id"] == "tmUSER42"


def test_exchange_code_fails_clearly_without_workspace():
    """워크스페이스 발견 실패 시 PAT 발급을 시도하지 않고 명확한 에러를 반환한다."""
    _prime_pending_state()
    with patch.object(st, "_post_form", return_value={"access_token": "at-123"}), \
         patch.object(st, "_get_json", side_effect=RuntimeError("403")), \
         patch.object(st, "_post_json") as mock_pat, \
         patch.object(st, "keychain_save") as mock_save:
        result = st.exchange_code("auth-code", state="test-state")

    assert result["ok"] is False
    assert "워크스페이스" in result["error"]
    mock_pat.assert_not_called()
    mock_save.assert_not_called()


def test_exchange_code_falls_back_to_next_team():
    """첫 팀에서 PAT 발급이 거부되면(400 등) 다음 팀에 시도한다."""
    _prime_pending_state()
    pat_urls = []

    def fake_post_json(url, payload, headers=None):
        pat_urls.append(url)
        if "tmGUEST" in url:
            raise RuntimeError("HTTP 400: pat not allowed")
        return {"token": "pat-ok", "pat": {}}

    with patch.object(st, "_post_form", return_value={"access_token": "at-123"}), \
         patch.object(st, "_get_json", return_value={"user": {"teams": [{"id": "tmGUEST"}, {"id": "tmMINE"}]}}), \
         patch.object(st, "_post_json", side_effect=fake_post_json), \
         patch.object(st, "keychain_save") as mock_save:
        result = st.exchange_code("auth-code", state="test-state")

    assert result["ok"] is True, result
    assert pat_urls == [f"{st._API_BASE}/auth/tmGUEST/pats", f"{st._API_BASE}/auth/tmMINE/pats"]
    saved = {c.args[0]: c.args[1] for c in mock_save.call_args_list}
    assert saved["workspace_id"] == "tmMINE"


def test_exchange_code_reports_all_team_errors():
    """모든 팀에서 실패하면 팀별 에러(HTTP 본문 포함)를 모아 반환한다."""
    _prime_pending_state()
    with patch.object(st, "_post_form", return_value={"access_token": "at-123"}), \
         patch.object(st, "_get_json", return_value={"user": {"teams": [{"id": "tmA"}]}}), \
         patch.object(st, "_post_json", side_effect=RuntimeError("HTTP 400: some api reason")), \
         patch.object(st, "keychain_save"):
        result = st.exchange_code("auth-code", state="test-state")
    assert result["ok"] is False
    assert "tmA" in result["error"] and "some api reason" in result["error"]


def test_no_hardcoded_workspace_id():
    """개발자 개인 워크스페이스 ID 하드코딩이 되살아나면 안 된다 (INT-1450/1451)."""
    src = Path(st.__file__).read_text(encoding="utf-8")
    assert "tmBp7DYU" not in src
    assert not hasattr(st, "WORKSPACE_ID")


def test_logout_clears_workspace_id():
    with patch.object(st, "keychain_delete") as mock_del:
        st.logout()
    deleted = {c.args[0] for c in mock_del.call_args_list}
    assert deleted == {"pat", "pat_expires_at", "workspace_id"}
