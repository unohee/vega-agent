# Created: 2026-07-02
# Purpose: KakaoTalk "send to me" integration tests (INT-2322) —
#   OAuth state fail-closed, token save/refresh, send tool payload, registry gate.
# Dependencies: pipeline/auth/kakao.py, pipeline/tools_kakao.py, pipeline/tool_registry.py
# Test Status: green (2026-07-02)

from __future__ import annotations

import json
import time
import urllib.parse

import pytest

import pipeline.auth.kakao as kakao
import pipeline.tools_kakao as tk


@pytest.fixture(autouse=True)
def _clean_pending_state():
    """_pending_state 가 테스트 간에 새지 않도록 전후 초기화."""
    kakao._pending_state.clear()
    yield
    kakao._pending_state.clear()


def _leak_probe(monkeypatch):
    """state 게이트가 유일한 차단선이 되도록 REST key 를 채우고, 게이트가 뚫리면
    토큰 교환·저장이 실제로 일어나게 하는 프로브. exchange_code 는 _token_request
    예외를 삼키므로(try/except → ok=False) AssertionError 방식은 mutation 을 못
    잡는다 — 호출/저장 기록으로 검증한다."""
    calls: list[dict] = []
    saved: dict[str, str] = {}
    monkeypatch.setattr(kakao, "_rest_key", lambda: "restkey")
    monkeypatch.setattr(kakao, "keychain_save", lambda a, v: saved.__setitem__(a, v))

    def fake_token_request(payload):
        calls.append(payload)
        return {"access_token": "leaked", "refresh_token": "leaked", "expires_in": 60}

    monkeypatch.setattr(kakao, "_token_request", fake_token_request)
    return calls, saved


# ── (1) state fail-closed ─────────────────────────────────────────────────────

class TestStateFailClosed:
    def test_state_mismatch_rejected(self, monkeypatch):
        calls, saved = _leak_probe(monkeypatch)
        kakao._pending_state["state"] = "expected"
        out = kakao.exchange_code("code", state="wrong")
        assert out["ok"] is False
        assert "state" in out["error"]
        assert calls == [] and saved == {}  # 교환·저장 미발생 (fail-closed)

    def test_missing_pending_rejected(self, monkeypatch):
        calls, saved = _leak_probe(monkeypatch)
        # authorize_url() 을 거치지 않은 콜백 — pending 부재 시 어떤 state 도 거부
        out = kakao.exchange_code("code", state="anything")
        assert out["ok"] is False
        assert calls == [] and saved == {}

    def test_none_state_rejected(self, monkeypatch):
        calls, saved = _leak_probe(monkeypatch)
        kakao._pending_state["state"] = "expected"
        out = kakao.exchange_code("code", state=None)
        assert out["ok"] is False
        assert calls == [] and saved == {}

    def test_authorize_url_sets_state_and_scope(self, monkeypatch):
        monkeypatch.setattr(kakao, "_rest_key", lambda: "restkey")
        url = kakao.authorize_url()
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["scope"] == ["talk_message"]
        assert q["response_type"] == ["code"]
        assert q["state"] == [kakao._pending_state["state"]]
        assert q["client_id"] == ["restkey"]

    def test_authorize_url_unconfigured_raises(self, monkeypatch):
        monkeypatch.setattr(kakao, "_rest_key", lambda: None)
        with pytest.raises(kakao.KakaoOAuthNotConfigured):
            kakao.authorize_url()


# ── (2) exchange_code → keychain 저장 ─────────────────────────────────────────

class TestExchangeCode:
    def test_tokens_saved_to_keychain(self, monkeypatch):
        saved: dict[str, str] = {}
        monkeypatch.setattr(kakao, "keychain_save", lambda a, v: saved.__setitem__(a, v))
        monkeypatch.setattr(kakao, "_rest_key", lambda: "restkey")
        seen: dict = {}

        def fake_token_request(payload):
            seen.update(payload)
            return {"access_token": "at1", "refresh_token": "rt1", "expires_in": 43199}

        monkeypatch.setattr(kakao, "_token_request", fake_token_request)
        kakao._pending_state["state"] = "s1"
        out = kakao.exchange_code("thecode", state="s1")
        assert out["ok"] is True
        assert saved["kakao_access"] == "at1"
        assert saved["kakao_refresh"] == "rt1"
        assert int(saved["kakao_expires_at"]) > int(time.time())
        # form 파라미터 계약
        assert seen["grant_type"] == "authorization_code"
        assert seen["code"] == "thecode"
        assert seen["redirect_uri"].endswith("/kakao/callback")
        # 성공 후 pending state 소진 (재사용 불가)
        assert not kakao._pending_state.get("state")

    def test_error_response_not_saved(self, monkeypatch):
        saved: dict[str, str] = {}
        monkeypatch.setattr(kakao, "keychain_save", lambda a, v: saved.__setitem__(a, v))
        monkeypatch.setattr(kakao, "_rest_key", lambda: "restkey")
        monkeypatch.setattr(kakao, "_token_request",
                            lambda p: {"error": "invalid_grant", "error_description": "bad code"})
        kakao._pending_state["state"] = "s1"
        out = kakao.exchange_code("thecode", state="s1")
        assert out["ok"] is False
        assert "bad code" in out["error"]
        assert not saved


# ── (3) access_token 만료 → refresh ───────────────────────────────────────────

class TestAccessTokenRefresh:
    def test_expired_triggers_refresh(self, monkeypatch):
        store = {
            "kakao_access": "old",
            "kakao_refresh": "rt1",
            "kakao_expires_at": str(int(time.time()) - 10),  # 이미 만료
        }
        saved: dict[str, str] = {}
        monkeypatch.setattr(kakao, "keychain_load", lambda a: store.get(a))
        monkeypatch.setattr(kakao, "keychain_save", lambda a, v: saved.__setitem__(a, v))
        monkeypatch.setattr(kakao, "_rest_key", lambda: "restkey")
        seen: dict = {}

        def fake_token_request(payload):
            seen.update(payload)
            return {"access_token": "fresh", "expires_in": 43199}

        monkeypatch.setattr(kakao, "_token_request", fake_token_request)
        assert kakao.access_token() == "fresh"
        assert seen["grant_type"] == "refresh_token"
        assert seen["refresh_token"] == "rt1"
        assert saved["kakao_access"] == "fresh"
        # 갱신 응답에 refresh_token 미동봉 → 기존 refresh 를 덮어쓰지 않는다
        assert "kakao_refresh" not in saved

    def test_valid_token_returned_without_refresh(self, monkeypatch):
        store = {
            "kakao_access": "valid",
            "kakao_expires_at": str(int(time.time()) + 3600),
        }
        monkeypatch.setattr(kakao, "keychain_load", lambda a: store.get(a))
        # refresh 경로가 호출되면 None 이 돌아와 아래 assert 가 실패한다
        monkeypatch.setattr(kakao, "refresh_access_token", lambda: None)
        assert kakao.access_token() == "valid"

    def test_refresh_rotates_when_new_refresh_included(self, monkeypatch):
        store = {"kakao_refresh": "rt-old"}
        saved: dict[str, str] = {}
        monkeypatch.setattr(kakao, "keychain_load", lambda a: store.get(a))
        monkeypatch.setattr(kakao, "keychain_save", lambda a, v: saved.__setitem__(a, v))
        monkeypatch.setattr(kakao, "_rest_key", lambda: "restkey")
        monkeypatch.setattr(kakao, "_token_request",
                            lambda p: {"access_token": "a2", "refresh_token": "rt-new",
                                       "expires_in": 43199})
        assert kakao.refresh_access_token() == "a2"
        assert saved["kakao_refresh"] == "rt-new"


# ── (4) kakao_send_to_me POST 계약 ────────────────────────────────────────────

class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestSendToMe:
    def _capture_urlopen(self, monkeypatch, body=b'{"result_code": 0}'):
        captured: dict = {}

        def fake_urlopen(req, timeout=None, context=None):
            captured["req"] = req
            return _FakeResp(body)

        # patch는 사용 모듈 기준 — tools_kakao 가 urllib.request.urlopen 을 호출
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        return captured

    def test_posts_correct_endpoint_and_template(self, monkeypatch):
        monkeypatch.setattr(tk._auth, "access_token", lambda: "tok")
        captured = self._capture_urlopen(monkeypatch)
        out = tk.kakao_send_to_me("안녕 메모")
        assert out == {"ok": True}
        req = captured["req"]
        assert req.full_url == "https://kapi.kakao.com/v2/api/talk/memo/default/send"
        assert req.get_header("Authorization") == "Bearer tok"
        form = urllib.parse.parse_qs(req.data.decode("utf-8"))
        template = json.loads(form["template_object"][0])
        assert template["object_type"] == "text"
        assert template["text"] == "안녕 메모"
        assert template["link"] == {}  # link_url 미지정 → 빈 객체

    def test_link_url_included(self, monkeypatch):
        monkeypatch.setattr(tk._auth, "access_token", lambda: "tok")
        captured = self._capture_urlopen(monkeypatch)
        tk.kakao_send_to_me("메모", link_url="https://example.com/x")
        form = urllib.parse.parse_qs(captured["req"].data.decode("utf-8"))
        template = json.loads(form["template_object"][0])
        assert template["link"] == {"web_url": "https://example.com/x",
                                    "mobile_web_url": "https://example.com/x"}

    def test_text_truncated_to_200(self, monkeypatch):
        monkeypatch.setattr(tk._auth, "access_token", lambda: "tok")
        captured = self._capture_urlopen(monkeypatch)
        tk.kakao_send_to_me("가" * 500)
        form = urllib.parse.parse_qs(captured["req"].data.decode("utf-8"))
        template = json.loads(form["template_object"][0])
        assert len(template["text"]) == 200

    def test_nonzero_result_code_raises(self, monkeypatch):
        monkeypatch.setattr(tk._auth, "access_token", lambda: "tok")
        self._capture_urlopen(monkeypatch, body=b'{"result_code": -1}')
        with pytest.raises(RuntimeError):
            tk.kakao_send_to_me("메모")


# ── (5) 미인증 시 RuntimeError ────────────────────────────────────────────────

class TestUnauthenticated:
    def test_no_token_raises(self, monkeypatch):
        monkeypatch.setattr(tk._auth, "access_token", lambda: None)

        def _boom(*a, **k):
            raise AssertionError("미인증 상태에서 네트워크 호출이 있으면 안 된다")

        monkeypatch.setattr(urllib.request, "urlopen", _boom)
        with pytest.raises(RuntimeError, match="연결"):
            tk.kakao_send_to_me("메모")


# ── (6) WORKSPACE_TOOLSETS 등록 + 스키마/함수 노출 ────────────────────────────

class TestRegistry:
    def test_workspace_toolset_registered(self):
        import pipeline.tool_registry as reg
        spec = reg.WORKSPACE_TOOLSETS["kakao"]
        assert spec["tools"] == ["kakao_send_to_me"]
        assert reg.toolset_of("kakao_send_to_me") == "kakao"
        assert "/kakao/auth" in spec["connect_hint"]

    def test_dispatch_gate_blocks_when_unauthenticated(self, monkeypatch):
        import pipeline.tool_registry as reg
        reg.invalidate_check_fn_cache()
        monkeypatch.setattr(kakao, "is_authenticated", lambda: False)
        err = reg.dispatch_gate("kakao_send_to_me")
        assert err is not None
        assert json.loads(err)["workspace_unavailable"] == "kakao"
        reg.invalidate_check_fn_cache()

    def test_schema_and_function_exposed(self):
        from pipeline import tools
        names = [s.get("name") for s in tools.TOOL_SCHEMAS]
        assert "kakao_send_to_me" in names
        assert tools.TOOL_FUNCTIONS["kakao_send_to_me"] is tk.kakao_send_to_me
