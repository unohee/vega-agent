# Created: 2026-06-11
# Purpose: Slack/Superthread 네이티브 도구 회귀 테스트 (INT-1456)
#          — 등록·toolset 게이트·Slack rotation 갱신·Superthread workspace 폴백.
# Dependencies: pytest, unittest.mock
# Test Status: passing

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── 등록 ──────────────────────────────────────────────────────────────────────

def test_tools_registered():
    from pipeline.tools import TOOL_SCHEMAS, TOOL_FUNCTIONS, _PLAN_BLOCKED_TOOLS
    names = {s["name"] for s in TOOL_SCHEMAS}
    expected = {
        "slack_search", "slack_list_channels", "slack_read_channel",
        "superthread_list_projects", "superthread_list_boards",
        "superthread_search_cards", "superthread_get_card", "superthread_create_card",
    }
    assert expected <= names
    assert expected <= set(TOOL_FUNCTIONS)
    # 쓰기 도구는 plan 모드 차단
    assert "superthread_create_card" in _PLAN_BLOCKED_TOOLS


def test_toolset_gate_hides_unauthenticated():
    """미연결 시 slack/superthread 스키마가 LLM에 노출되지 않아야 한다."""
    from pipeline import tool_registry as tr
    schemas = [{"name": "slack_search"}, {"name": "superthread_get_card"}, {"name": "web_search"}]
    with patch("pipeline.auth.slack.is_authenticated", return_value=False), \
         patch("pipeline.auth.superthread.is_authenticated", return_value=False):
        tr.invalidate_check_fn_cache()
        out = tr.filter_available_schemas(schemas)
    tr.invalidate_check_fn_cache()
    assert [s["name"] for s in out] == ["web_search"]
    gate = tr.dispatch_gate("slack_search")
    assert gate is None or "slack" in gate  # 캐시 상태 무관 — 형태만 확인


# ── Slack token rotation ─────────────────────────────────────────────────────

def test_slack_user_token_refreshes_when_expired():
    from pipeline.auth import slack as sl
    store = {
        "user": "xoxp-old",
        "user_refresh": "xoxe-refresh",
        "user_expires_at": str(int(time.time()) - 10),  # 이미 만료
    }
    with patch.object(sl, "keychain_load", side_effect=store.get), \
         patch.object(sl, "keychain_save") as mock_save, \
         patch.object(sl, "_load_client", return_value={"client_id": "c", "client_secret": "s"}), \
         patch.object(sl, "_post_token_endpoint", return_value={
             "ok": True, "token_type": "user",
             "access_token": "xoxp-new", "refresh_token": "xoxe-new", "expires_in": 43200,
         }):
        token = sl.user_token()
    assert token == "xoxp-new"
    saved = {c.args[0]: c.args[1] for c in mock_save.call_args_list}
    assert saved["user"] == "xoxp-new"
    assert saved["user_refresh"] == "xoxe-new"
    assert "user_expires_at" in saved


def test_slack_user_token_passthrough_without_expiry():
    """rotation 미사용(만료 정보 없음)이면 저장된 토큰 그대로."""
    from pipeline.auth import slack as sl
    with patch.object(sl, "keychain_load", side_effect={"user": "xoxp-plain"}.get):
        assert sl.user_token() == "xoxp-plain"


def test_slack_api_retries_on_token_expired():
    from pipeline import tools_slack as ts
    calls = []

    def fake_call(method, params=None, *, token):
        calls.append(token)
        if token == "xoxp-old":
            return {"ok": False, "error": "token_expired"}
        return {"ok": True, "channels": []}

    with patch.object(ts._auth, "user_token", return_value="xoxp-old"), \
         patch.object(ts._auth, "refresh_user_token", return_value="xoxp-new"), \
         patch.object(ts, "_call", side_effect=fake_call):
        out = ts.slack_list_channels()
    assert out == []
    assert calls == ["xoxp-old", "xoxp-new"]


def test_slack_api_clear_error_when_refresh_impossible():
    from pipeline import tools_slack as ts
    with patch.object(ts._auth, "user_token", return_value="xoxp-old"), \
         patch.object(ts._auth, "refresh_user_token", return_value=None), \
         patch.object(ts, "_call", return_value={"ok": False, "error": "token_expired"}):
        with pytest.raises(RuntimeError, match="다시 연결"):
            ts.slack_list_channels()


# ── Superthread ──────────────────────────────────────────────────────────────

def test_superthread_workspace_fallback_discovers_and_stores():
    """구버전 연결(workspace_id 미저장)이면 users/me 로 발견해 Keychain 에 저장."""
    from pipeline import tools_superthread as st
    with patch.object(st._auth, "stored_workspace_id", return_value=None), \
         patch.object(st._auth, "pat_token", return_value="pat-x"), \
         patch.object(st._auth, "_discover_workspace_id", return_value="tmUSER"), \
         patch.object(st._auth, "keychain_save") as mock_save:
        assert st._workspace_id() == "tmUSER"
    mock_save.assert_called_once_with("workspace_id", "tmUSER")


def test_superthread_create_card_due_date():
    from pipeline import tools_superthread as st
    captured = {}

    def fake_st(path, method="GET", body=None, params=None):
        captured.update({"path": path, "method": method, "body": body})
        return {"card": {"id": "9", "title": "t"}}

    with patch.object(st, "_st", side_effect=fake_st):
        out = st.superthread_create_card("t", "b1", "l1", due_date="2026-07-01")
    assert out["id"] == "9"
    assert captured["method"] == "POST" and captured["path"] == "cards"
    assert isinstance(captured["body"]["due_date"], int)  # ms timestamp
    with patch.object(st, "_st", side_effect=fake_st):
        with pytest.raises(RuntimeError, match="due_date"):
            st.superthread_create_card("t", "b1", "l1", due_date="07/01")


def test_superthread_create_card_converts_markdown_to_html():
    """INT-1571: content 마크다운이 Superthread(HTML 필드)용 시맨틱 HTML 로 변환돼 POST.

    버그 재현: 마크다운 원문이 그대로 POST 되면 헤딩·리스트가 한 줄로 뭉쳐 보였다.
    """
    from pipeline import tools_superthread as st
    captured = {}

    def fake_st(path, method="GET", body=None, params=None):
        captured.update({"body": body})
        return {"card": {"id": "9", "title": "t"}}

    md = "## 제목\n- 항목 1\n- 항목 2\n\n**굵게** 와 [링크](https://e.com)"
    with patch.object(st, "_st", side_effect=fake_st):
        st.superthread_create_card("t", "b1", "l1", content=md)
    html = captured["body"]["content"]
    # 마크다운 원문이 그대로 들어가면 버그 (회귀 방지)
    assert "## 제목" not in html
    assert "- 항목 1" not in html
    # 시맨틱 HTML 로 변환됐는지
    assert "<h2>" in html and "제목" in html
    assert "<li>" in html and "항목 1" in html
    assert "<strong>" in html
    assert 'href="https://e.com"' in html


def test_content_to_html_idempotent_and_edge():
    """이미 HTML 이면 그대로(이중변환 방지), 빈/공백 입력은 그대로 둔다."""
    from pipeline.tools_superthread import _content_to_html
    assert _content_to_html("<p>already <b>x</b></p>") == "<p>already <b>x</b></p>"
    assert _content_to_html("") == ""
    assert _content_to_html("   ") == "   "
    out = _content_to_html("plain line")
    assert "plain line" in out  # 순수 텍스트도 깨지지 않음


def test_content_to_html_fallback_without_markdown(monkeypatch):
    """markdown 라이브러리 부재(구 배포본 등) 시 _md_to_html 폴백 — 여전히 HTML 생성."""
    import builtins
    real_import = builtins.__import__

    def block_markdown(name, *a, **k):
        if name == "markdown":
            raise ImportError("blocked for test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", block_markdown)
    from pipeline.tools_superthread import _content_to_html
    out = _content_to_html("## 제목\n본문 텍스트")
    assert "<" in out and "제목" in out  # 폴백도 HTML 태그 생성


# ── Airtable (INT-1498 / INT-1570) ─────────────────────────────────────────────

def test_airtable_tools_registered():
    from pipeline.tools import TOOL_SCHEMAS, TOOL_FUNCTIONS, _PLAN_BLOCKED_TOOLS
    names = {s["name"] for s in TOOL_SCHEMAS}
    expected = {
        "airtable_list_bases", "airtable_list_tables", "airtable_list_records",
        "airtable_get_records", "airtable_create_record", "airtable_update_record",
    }
    assert expected <= names
    assert expected <= set(TOOL_FUNCTIONS)
    # 쓰기 도구는 plan 모드 차단
    assert {"airtable_create_record", "airtable_update_record"} <= _PLAN_BLOCKED_TOOLS


def test_airtable_toolset_gate_hides_unauthenticated():
    from pipeline import tool_registry as tr
    schemas = [{"name": "airtable_list_records"}, {"name": "web_search"}]
    with patch("pipeline.auth.airtable.is_authenticated", return_value=False):
        tr.invalidate_check_fn_cache()
        out = tr.filter_available_schemas(schemas)
    tr.invalidate_check_fn_cache()
    assert [s["name"] for s in out] == ["web_search"]


def test_airtable_get_records_batches_by_record_id():
    """INT-1570: linked field 의 record ID 목록 → RECORD_ID() formula 로 실제 값 조회."""
    from pipeline import tools_airtable as ta

    class FakeTable:
        def __init__(self):
            self.calls = []

        def all(self, formula=None, **kw):
            import re
            self.calls.append(formula)
            ids = re.findall(r"rec\w+", formula or "")
            return [{"id": i, "fields": {"amount": 100, "rid": i}} for i in ids]

    ft = FakeTable()

    class FakeApi:
        def table(self, b, t):
            return ft

    with patch.object(ta, "_api", return_value=FakeApi()):
        out = ta.airtable_get_records("app", "tbl", ["rec1", "rec2", "rec3"])
    assert out["count"] == 3
    assert "RECORD_ID()='rec1'" in ft.calls[0]
    # linked ID 만 받던 것이 실제 필드 값으로 채워졌는지 (버그 회귀 방지)
    assert out["records"][0]["fields"] == {"amount": 100, "rid": "rec1"}


def test_airtable_requires_pat():
    """미연결 시 명확한 에러 — 조용히 빈 결과 반환 금지."""
    from pipeline import tools_airtable as ta
    with patch("pipeline.auth.airtable.token", return_value=None):
        with pytest.raises(RuntimeError, match="Airtable 미연결"):
            ta.airtable_list_bases()


# ── GitHub (INT-1498) ──────────────────────────────────────────────────────────

def test_github_tools_registered():
    from pipeline.tools import TOOL_SCHEMAS, TOOL_FUNCTIONS, _PLAN_BLOCKED_TOOLS
    names = {s["name"] for s in TOOL_SCHEMAS}
    expected = {
        "github_list_issues", "github_get_issue", "github_create_issue",
        "github_list_pulls", "github_get_pull", "github_search_code", "github_read_file",
    }
    assert expected <= names
    assert expected <= set(TOOL_FUNCTIONS)
    assert "github_create_issue" in _PLAN_BLOCKED_TOOLS


def test_github_toolset_gate_hides_unauthenticated():
    from pipeline import tool_registry as tr
    schemas = [{"name": "github_read_file"}, {"name": "web_search"}]
    with patch("pipeline.auth.github.is_authenticated", return_value=False):
        tr.invalidate_check_fn_cache()
        out = tr.filter_available_schemas(schemas)
    tr.invalidate_check_fn_cache()
    assert [s["name"] for s in out] == ["web_search"]


def test_github_list_issues_excludes_prs():
    """issues 엔드포인트는 PR 도 반환 — pull_request 키로 필터해야 한다 (실측 확인)."""
    from pipeline import tools_github as tg
    fake = [
        {"number": 1, "title": "real issue", "state": "open", "labels": [],
         "user": {"login": "u"}, "comments": 0, "html_url": "x"},
        {"number": 2, "title": "a PR", "state": "open", "labels": [],
         "user": {"login": "u"}, "comments": 0, "html_url": "y", "pull_request": {"url": "..."}},
    ]
    with patch.object(tg, "_gh", return_value=fake):
        out = tg.github_list_issues("o/r")
    assert [i["number"] for i in out] == [1]  # PR(2)은 제외


def test_github_requires_token():
    from pipeline import tools_github as tg
    with patch("pipeline.auth.github.token", return_value=None):
        with pytest.raises(RuntimeError, match="GitHub 미연결"):
            tg.github_list_issues("o/r")


# ── tool group 마이그레이션 ───────────────────────────────────────────────────

def test_new_groups_enabled_for_legacy_file(tmp_path, monkeypatch):
    """known 필드가 없는 구버전 tool_groups.json — 새 그룹(slack/superthread)은 기본 활성."""
    from pipeline import llm_gateway as lg
    p = tmp_path / "tool_groups.json"
    p.write_text(json.dumps({"enabled": ["web", "memory"]}))  # 구버전: known 없음
    monkeypatch.setattr(lg, "_TOOL_GROUPS_PATH", p)
    enabled = lg.get_enabled_groups()
    assert {"slack", "superthread", "web", "memory"} <= enabled
    assert "gmail" not in enabled  # 사용자가 끈(목록에 없는) 레거시 그룹은 그대로 꺼짐


def test_new_groups_respect_explicit_disable(tmp_path, monkeypatch):
    """known 에 slack 이 있는데 enabled 에 없으면 — 사용자가 명시적으로 끈 것, 유지."""
    from pipeline import llm_gateway as lg
    p = tmp_path / "tool_groups.json"
    p.write_text(json.dumps({"enabled": ["web"], "known": sorted(lg._ALL_GROUPS)}))
    monkeypatch.setattr(lg, "_TOOL_GROUPS_PATH", p)
    enabled = lg.get_enabled_groups()
    assert "slack" not in enabled and "superthread" not in enabled
    assert enabled == {"web"}


# 연결된 워크스페이스의 읽기 도구가 light load 에서 사라지던 회귀 방지.
# (route_load 가 80자 이하 메시지를 light 로 분류 → get_schemas_for_mode(load="light")
#  가 _LIGHT_ALLOWED_TOOLS 로 축소. 짧은 "슬랙 봐줘" 가 slack 도구를 통째로 잃었었음.)
_WORKSPACE_READ_IN_LIGHT = [
    "slack_search", "slack_list_channels", "slack_read_channel",
    "superthread_list_projects", "superthread_list_boards",
    "superthread_search_cards", "superthread_get_card",
    "gmail_read", "drive_search", "drive_read",
    "linear_search_issues", "linear_list_issues",
    "airtable_list_records", "airtable_get_records",
    "github_search_code", "github_list_issues",
]
# light 에서 절대 노출되면 안 되는 워크스페이스 *쓰기/외부전송* 도구.
_WORKSPACE_WRITE_NOT_IN_LIGHT = [
    "slack_send_message", "superthread_create_card", "gmail_send",
    "calendar_create_event", "linear_create_issue", "airtable_create_record",
    "github_create_issue", "docs_create", "slides_create",
]


@pytest.mark.parametrize("tool", _WORKSPACE_READ_IN_LIGHT)
def test_light_load_exposes_connected_workspace_read_tools(tool):
    """light load 도 '단순 조회'이므로 연결 워크스페이스 읽기 도구는 노출돼야 한다.

    핵심 불변식은 allowlist 멤버십(항상 성립). 실제 payload 노출은 그 도구가
    TOOL_SCHEMAS 에 등록돼 있을 때만 검사한다 — linear 네이티브 도구처럼
    LINEAR_API_KEY 유무에 따라 조건부 등록되는 toolset 이 있기 때문."""
    from pipeline.tools import _LIGHT_ALLOWED_TOOLS, TOOL_SCHEMAS, get_schemas_for_mode
    assert tool in _LIGHT_ALLOWED_TOOLS, f"{tool} 가 _LIGHT_ALLOWED_TOOLS 에 없음 (light 에서 증발)"
    base = {s["name"] for s in TOOL_SCHEMAS}
    if tool not in base:
        pytest.skip(f"{tool} 미등록(조건부 toolset) — allowlist 멤버십만 검사")
    with patch("pipeline.tool_registry.is_toolset_available", return_value=True):
        names = {s["name"] for s in get_schemas_for_mode(TOOL_SCHEMAS, load="light")}
    assert tool in names, f"{tool} 가 light 스키마 payload 에서 빠짐"


@pytest.mark.parametrize("tool", _WORKSPACE_WRITE_NOT_IN_LIGHT)
def test_light_load_excludes_workspace_write_tools(tool):
    """light 는 토큰 절감 모드 — 워크스페이스 쓰기/외부전송 도구는 제외 유지."""
    from pipeline.tools import _LIGHT_ALLOWED_TOOLS
    assert tool not in _LIGHT_ALLOWED_TOOLS, f"{tool}(쓰기)가 light 에 잘못 포함됨"


def test_superthread_revoke_pats_only_deletes_same_name():
    """PAT 발급 전 정리(INT-1994) — 동명('vega-agent') PAT 만 삭제, 타 앱 PAT 는 보존."""
    from pipeline.auth import superthread as st
    listing = {"pats": [
        {"id": "a1", "name": "vega-agent"},
        {"id": "b2", "name": "kyte-portal"},   # 다른 앱 — 보존돼야
        {"id": "c3", "name": "vega-agent"},
        {"id": "d4", "name": "vega-core"},     # 옛 이름 — 동명 아님, 보존
    ]}
    deleted = []
    with patch.object(st, "_get_json", return_value=listing), \
         patch.object(st, "_delete", side_effect=lambda url, headers=None: deleted.append(url)):
        n = st._revoke_pats_named("WS1", "tok", "vega-agent")
    assert n == 2
    assert all(u.endswith("/pats/a1") or u.endswith("/pats/c3") for u in deleted)
    assert not any("b2" in u or "d4" in u for u in deleted)


def test_superthread_revoke_best_effort_swallows_errors():
    """조회/삭제 실패가 발급을 막지 않도록 — 예외를 삼키고 0 반환."""
    from pipeline.auth import superthread as st
    with patch.object(st, "_get_json", side_effect=RuntimeError("HTTP 500")):
        assert st._revoke_pats_named("WS1", "tok", "vega-agent") == 0


# ── 카드 4236: light 가 MCP(kyte__/superthread__) read 도구를 잃던 버그 ──────────

def test_light_load_includes_mcp_read_excludes_mcp_writes():
    """정적 allowlist 로 못 잡는 MCP 도구도 read 면 light 에 노출, write 면 제외.
    (kyte 봇 short 질문이 light 로 분류되며 kyte__ 도구를 통째로 잃어 정적 응답만 뱉던 버그.)"""
    from pipeline.tools import get_schemas_for_mode
    base = [
        {"type": "function", "name": "kyte__find_tasks", "parameters": {}},
        {"type": "function", "name": "kyte__create_task", "parameters": {}},
        {"type": "function", "name": "superthread__getTask", "parameters": {}},
        {"type": "function", "name": "superthread__task_update", "parameters": {}},
    ]
    with patch("pipeline.tool_registry.is_toolset_available", return_value=True):
        light = {s["name"] for s in get_schemas_for_mode(base, load="light")}
    assert "kyte__find_tasks" in light and "superthread__getTask" in light
    assert "kyte__create_task" not in light and "superthread__task_update" not in light


# ── 카드 4295: gmail draft 수정이 새 draft 를 양산하던 버그 ──────────────────────

def test_gmail_draft_creates_then_updates_in_place():
    """draft_id 없으면 create(POST /drafts), 있으면 update(PUT /drafts/{id})."""
    from pipeline import tools_google as G
    calls = []
    with patch.object(G, "_gapi",
                      side_effect=lambda path, **k: calls.append((k.get("method"), path)) or {"id": "d1"}):
        G.gmail_draft("a@b.c", "s", "body")
        G.gmail_draft("a@b.c", "s", "body2", draft_id="d1")
    assert calls[0] == ("POST", "gmail.googleapis.com/gmail/v1/users/me/drafts")
    assert calls[1] == ("PUT", "gmail.googleapis.com/gmail/v1/users/me/drafts/d1")


# ── 카드 4297: 여러 메일 첨부 batch 수집 ────────────────────────────────────────

def test_gmail_collect_attachments_batches_and_dedups(tmp_path):
    """검색→메일별 list→download 를 단일 호출로 수행 + 파일명 충돌 회피."""
    import base64
    from pipeline import tools_google as G

    def fake_gapi(path, account="", params=None, method="GET", body=None):
        if path.endswith("/messages"):
            assert "has:attachment" in params["q"]   # 자동 추가 확인
            return {"messages": [{"id": "m1"}, {"id": "m2"}]}
        if "/attachments/" in path:
            return {"data": base64.urlsafe_b64encode(b"X").decode()}
        # messages/{id} full → 같은 이름(report.pdf) 첨부 하나씩
        mid = path.rsplit("/", 1)[-1]
        return {"payload": {"parts": [{"filename": "report.pdf", "mimeType": "application/pdf",
                                       "body": {"attachmentId": f"a-{mid}", "size": 1}}]}}

    with patch.object(G, "_gapi", side_effect=fake_gapi):
        res = G.gmail_collect_attachments("from:x", str(tmp_path))
    assert res["count"] == 2
    assert sorted(f["filename"] for f in res["files"]) == ["report (2).pdf", "report.pdf"]
    assert (tmp_path / "report.pdf").exists() and (tmp_path / "report (2).pdf").exists()
