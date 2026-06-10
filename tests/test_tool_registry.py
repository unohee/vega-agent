# Created: 2026-06-10
# Purpose: pipeline/tool_registry.py 단위 테스트 — 워크스페이스 가용성 게이트 (INT-1428)
# Dependencies: pipeline/tool_registry.py, pipeline/tools.py
# Test Status: green (2026-06-10)

from __future__ import annotations

import json

import pytest

import pipeline.tool_registry as reg


@pytest.fixture(autouse=True)
def _fresh_cache():
    """TTL 캐시가 테스트 간에 새지 않도록 전후 무효화."""
    reg.invalidate_check_fn_cache()
    yield
    reg.invalidate_check_fn_cache()


def _set_google_auth(monkeypatch, value: bool):
    # _google_check가 호출 시점에 lazy import하므로 정의 모듈을 patch한다
    import pipeline.auth.google as g
    monkeypatch.setattr(g, "is_authenticated", lambda: value)


# ── toolset 매핑 ──────────────────────────────────────────────────────────────

class TestToolsetOf:
    def test_google_tools_mapped(self):
        assert reg.toolset_of("gmail_search") == "google"
        assert reg.toolset_of("calendar_create_event") == "google"
        assert reg.toolset_of("docs_append") == "google"

    def test_core_tools_unmapped(self):
        assert reg.toolset_of("web_search") is None
        assert reg.toolset_of("bash_exec") is None
        # 로컬 파일 도구는 tools_google.py에 있어도 Google 인증과 무관
        assert reg.toolset_of("file_read") is None
        assert reg.toolset_of("icloud_list") is None

    def test_mcp_tools_unmapped(self):
        assert reg.toolset_of("linear__list_issues") is None


# ── 스키마 필터 ────────────────────────────────────────────────────────────────

_SCHEMAS = [
    {"type": "function", "name": "web_search"},
    {"type": "function", "name": "gmail_search"},
    {"type": "function", "name": "calendar_list_events"},
    {"type": "function", "name": "some_mcp__tool"},
]


class TestFilterAvailableSchemas:
    def test_unauthenticated_google_filtered(self, monkeypatch):
        _set_google_auth(monkeypatch, False)
        names = [s["name"] for s in reg.filter_available_schemas(_SCHEMAS)]
        assert "gmail_search" not in names
        assert "calendar_list_events" not in names
        assert "web_search" in names
        assert "some_mcp__tool" in names

    def test_authenticated_google_passes(self, monkeypatch):
        _set_google_auth(monkeypatch, True)
        names = [s["name"] for s in reg.filter_available_schemas(_SCHEMAS)]
        assert "gmail_search" in names
        assert "calendar_list_events" in names

    def test_get_schemas_for_mode_applies_filter(self, monkeypatch):
        _set_google_auth(monkeypatch, False)
        from pipeline.tools import get_schemas_for_mode
        names = [s["name"] for s in get_schemas_for_mode(_SCHEMAS)]
        assert "gmail_search" not in names
        assert "web_search" in names


# ── 디스패치 게이트 ────────────────────────────────────────────────────────────

class TestDispatchGate:
    def test_available_passes(self, monkeypatch):
        _set_google_auth(monkeypatch, True)
        assert reg.dispatch_gate("gmail_search") is None

    def test_non_workspace_tool_passes(self, monkeypatch):
        _set_google_auth(monkeypatch, False)
        assert reg.dispatch_gate("web_search") is None

    def test_unavailable_blocked_with_hint(self, monkeypatch):
        _set_google_auth(monkeypatch, False)
        err = reg.dispatch_gate("gmail_send")
        assert err is not None
        parsed = json.loads(err)
        assert parsed["workspace_unavailable"] == "google"
        assert "연결" in parsed["error"]

    def test_dispatch_tool_blocks_without_calling_fn(self, monkeypatch):
        _set_google_auth(monkeypatch, False)
        import pipeline.tools as tools

        def _boom(**kwargs):
            raise AssertionError("미연결 상태에서 도구 본체가 호출되면 안 된다")

        monkeypatch.setitem(tools.TOOL_FUNCTIONS, "gmail_search", _boom)
        result = json.loads(tools.dispatch_tool("gmail_search", {"query": "x"}))
        assert result["workspace_unavailable"] == "google"


# ── check_fn TTL 캐시 (hermes-agent tools/registry.py 차용 부분) ─────────────────

class TestCheckFnCache:
    def test_result_cached_until_invalidated(self):
        state = {"value": True}
        fn = lambda: state["value"]  # noqa: E731
        assert reg._check_fn_cached(fn) is True
        state["value"] = False
        # TTL(30s) 이내 — 캐시 히트
        assert reg._check_fn_cached(fn) is True
        reg.invalidate_check_fn_cache()
        assert reg._check_fn_cached(fn) is False

    def test_exception_means_unavailable(self):
        def _raises():
            raise RuntimeError("keychain down")
        assert reg._check_fn_cached(_raises) is False

    def test_check_fn_exception_via_public_api(self, monkeypatch):
        import pipeline.auth.google as g
        def _raises():
            raise RuntimeError("keychain down")
        monkeypatch.setattr(g, "is_authenticated", _raises)
        assert reg.is_toolset_available("google") is False

    def test_unknown_toolset_available(self):
        # check_fn 없는/미등록 toolset은 가용 (hermes 동일 semantics)
        assert reg.is_toolset_available("nonexistent") is True
