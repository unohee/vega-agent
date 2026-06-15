# Created: 2026-06-15
# Purpose: pipeline/tools_google.py 커버리지 6% → 30% (INT-1528)
# Dependencies: pipeline/tools_google.py, monkeypatch _gapi
# Test Status: 신규

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _gapi 헬퍼  (L19-66)
# ---------------------------------------------------------------------------
class TestGapi:
    """_gapi — monkeypatch get_access_token + urlopen"""

    def _call(self, path: str, token: str | None = "tok", response_body: bytes = b'{"ok":1}',
              http_error: urllib.error.HTTPError | None = None, params=None, body=None):
        from pipeline.tools_google import _gapi

        def fake_token(account=""):
            return token

        class FakeResp:
            def read(self):
                return response_body
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def fake_urlopen(req, timeout, context):
            if http_error:
                raise http_error
            return FakeResp()

        with patch("pipeline.tools_google._google_token", side_effect=fake_token):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                return _gapi(path, params=params, body=body)

    def test_normal_response_returns_dict(self):
        result = self._call("/v1/messages", response_body=b'{"messages": []}')
        assert result == {"messages": []}

    def test_empty_response_returns_empty_dict(self):
        result = self._call("/v1/thing", response_body=b"")
        assert result == {}

    def test_no_token_raises_runtime_error(self):
        from pipeline.tools_google import _gapi
        with patch("pipeline.tools_google._google_token", return_value=None):
            with pytest.raises(RuntimeError, match="Google OAuth"):
                _gapi("https://api.test/v1/foo")

    def test_http_401_raises_with_body(self):
        err = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)
        err.read = lambda: b'{"error": "invalid_grant"}'
        from pipeline.tools_google import _gapi
        with patch("pipeline.tools_google._google_token", return_value="tok"):
            with patch("urllib.request.urlopen", side_effect=err):
                with pytest.raises(RuntimeError, match="Google API HTTP 401"):
                    _gapi("https://api.test/v1/resource")

    def test_http_400_raises_with_body(self):
        err = urllib.error.HTTPError("http://x", 400, "Bad Request", {}, None)
        err.read = lambda: b'{"error": "bad_request"}'
        from pipeline.tools_google import _gapi
        with patch("pipeline.tools_google._google_token", return_value="tok"):
            with patch("urllib.request.urlopen", side_effect=err):
                with pytest.raises(RuntimeError, match="Google API HTTP 400"):
                    _gapi("https://api.test/v1/resource")

    def test_params_list_value_expanded(self):
        """list 파라미터는 같은 키를 반복해 URL encode."""
        captured = []

        class FakeResp:
            def read(self): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def fake_urlopen(req, timeout, context):
            captured.append(req.full_url)
            return FakeResp()

        from pipeline.tools_google import _gapi
        with patch("pipeline.tools_google._google_token", return_value="tok"):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                _gapi("https://api.test/v1/q", params={"metadataHeaders": ["From", "Subject"]})

        url = captured[0]
        assert "metadataHeaders=From" in url
        assert "metadataHeaders=Subject" in url

    def test_post_body_sets_content_type(self):
        captured_headers = []

        class FakeResp:
            def read(self): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def fake_urlopen(req, timeout, context):
            captured_headers.append(dict(req.headers))
            return FakeResp()

        from pipeline.tools_google import _gapi
        with patch("pipeline.tools_google._google_token", return_value="tok"):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                _gapi("https://api.test/v1/q", method="POST", body={"key": "val"})

        assert "Content-type" in captured_headers[0]

    def test_none_param_values_skipped(self):
        captured = []

        class FakeResp:
            def read(self): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def fake_urlopen(req, timeout, context):
            captured.append(req.full_url)
            return FakeResp()

        from pipeline.tools_google import _gapi
        with patch("pipeline.tools_google._google_token", return_value="tok"):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                _gapi("https://api.test/v1/q", params={"q": "hello", "pageToken": None})

        url = captured[0]
        assert "pageToken" not in url
        assert "q=hello" in url


# ---------------------------------------------------------------------------
# gmail_search  (L71-90)
# ---------------------------------------------------------------------------
class TestGmailSearch:
    def _mock_gapi(self, messages_resp: dict, detail_resp: dict):
        call_count = [0]

        def fake_gapi(path, account="", params=None, method="GET", body=None):
            call_count[0] += 1
            if "messages" in path and call_count[0] == 1:
                return messages_resp
            return detail_resp

        return patch("pipeline.tools_google._gapi", side_effect=fake_gapi)

    def test_no_messages_returns_empty(self):
        with self._mock_gapi({"messages": []}, {}):
            from pipeline.tools_google import gmail_search
            result = gmail_search("test query")
        assert result == []

    def test_returns_message_with_headers(self):
        messages_resp = {"messages": [{"id": "m1", "threadId": "t1"}]}
        detail_resp = {
            "id": "m1", "threadId": "t1", "snippet": "Hello world",
            "payload": {"headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "Subject", "value": "Test subject"},
                {"name": "Date", "value": "Mon, 1 Jan 2026"},
            ]},
        }
        with self._mock_gapi(messages_resp, detail_resp):
            from pipeline.tools_google import gmail_search
            result = gmail_search("test")
        assert len(result) == 1
        assert result[0]["from"] == "alice@example.com"
        assert result[0]["subject"] == "Test subject"
        assert result[0]["snippet"] == "Hello world"


# ---------------------------------------------------------------------------
# _html_to_text  (L93-104)
# ---------------------------------------------------------------------------
class TestHtmlToText:
    def _fn(self):
        from pipeline.tools_google import _html_to_text
        return _html_to_text

    def test_strips_tags(self):
        result = self._fn()("<p>Hello <b>world</b></p>")
        assert "<" not in result
        assert "Hello" in result and "world" in result

    def test_removes_script_blocks(self):
        result = self._fn()("<script>alert('xss')</script><p>Safe</p>")
        assert "alert" not in result
        assert "Safe" in result

    def test_removes_style_blocks(self):
        result = self._fn()("<style>.cls{color:red}</style>Content")
        assert ".cls" not in result
        assert "Content" in result

    def test_br_becomes_newline(self):
        result = self._fn()("line1<br>line2")
        assert "\n" in result

    def test_html_entities_unescaped(self):
        result = self._fn()("Hello &amp; world")
        assert "&" in result


# ---------------------------------------------------------------------------
# gmail_read  (L107-148) — body 추출 분기
# ---------------------------------------------------------------------------
class TestGmailRead:
    def _fake_detail(self, plain: str = "", html: str = "", snippet: str = ""):
        import base64

        def _encode(text: str) -> str:
            return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")

        parts = []
        if plain:
            parts.append({"mimeType": "text/plain", "body": {"data": _encode(plain)}})
        if html:
            parts.append({"mimeType": "text/html", "body": {"data": _encode(html)}})

        return {
            "snippet": snippet,
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "Subject", "value": "Test"},
                    {"name": "From", "value": "sender@test.com"},
                    {"name": "Date", "value": "2026-01-01"},
                ],
                "body": {"data": ""},
                "parts": parts,
            },
        }

    def test_plain_text_body_preferred(self):
        detail = self._fake_detail(plain="plain body", html="<b>html</b>")
        with patch("pipeline.tools_google._gapi", return_value=detail):
            from pipeline.tools_google import gmail_read
            result = gmail_read("m1")
        assert "plain body" in result["body"]

    def test_html_body_used_when_no_plain(self):
        detail = self._fake_detail(html="<p>html only</p>")
        with patch("pipeline.tools_google._gapi", return_value=detail):
            from pipeline.tools_google import gmail_read
            result = gmail_read("m1")
        assert "html only" in result["body"]

    def test_snippet_fallback_when_no_body(self):
        detail = self._fake_detail(snippet="snippet text")
        with patch("pipeline.tools_google._gapi", return_value=detail):
            from pipeline.tools_google import gmail_read
            result = gmail_read("m1")
        assert "snippet text" in result["body"]

    def test_truncation_flag_set_when_long(self):
        detail = self._fake_detail(plain="x" * 30000)
        with patch("pipeline.tools_google._gapi", return_value=detail):
            from pipeline.tools_google import gmail_read
            result = gmail_read("m1", max_chars=100)
        assert result["truncated"] is True
        assert len(result["body"]) == 100

    def test_no_truncation_when_short(self):
        detail = self._fake_detail(plain="short")
        with patch("pipeline.tools_google._gapi", return_value=detail):
            from pipeline.tools_google import gmail_read
            result = gmail_read("m1")
        assert result["truncated"] is False


# ---------------------------------------------------------------------------
# calendar_list_events  (실제 함수 이름 확인 후 테스트)
# ---------------------------------------------------------------------------
class TestCalendarListEvents:
    def test_returns_events(self):
        cal_list_resp = {"items": [{"id": "primary", "summary": "주 캘린더"}]}
        events_resp = {
            "items": [
                {"summary": "팀 미팅",
                 "start": {"dateTime": "2026-06-15T10:00:00+09:00"},
                 "end": {"dateTime": "2026-06-15T11:00:00+09:00"},
                 "status": "confirmed"},
            ]
        }
        call_count = [0]

        def fake_gapi(path, account="", params=None, method="GET", body=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return cal_list_resp  # _calendar_ids
            return events_resp        # events per calendar

        with patch("pipeline.tools_google._gapi", side_effect=fake_gapi):
            from pipeline.tools_google import calendar_list_events
            result = calendar_list_events()

        assert len(result) >= 1
        assert result[0]["summary"] == "팀 미팅"

    def test_no_calendars_returns_empty(self):
        with patch("pipeline.tools_google._gapi", return_value={"items": []}):
            from pipeline.tools_google import calendar_list_events
            result = calendar_list_events()
        assert result == []

    def test_calendar_name_filter(self):
        cal_list_resp = {"items": [
            {"id": "cal1", "summary": "Work"},
            {"id": "cal2", "summary": "Personal"},
        ]}
        events_resp = {"items": [{"summary": "Work meeting", "start": {"dateTime": "2026-06-15T10:00:00Z"}, "end": {"dateTime": "2026-06-15T11:00:00Z"}, "status": "confirmed"}]}
        call_count = [0]

        def fake_gapi(path, account="", params=None, method="GET", body=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return cal_list_resp
            return events_resp

        with patch("pipeline.tools_google._gapi", side_effect=fake_gapi):
            from pipeline.tools_google import calendar_list_events
            result = calendar_list_events(calendar_name="Work")

        # Work 캘린더만 조회됐으므로 call_count == 2
        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# _md_to_html  (L203-258) — 순수 함수, 외부 의존 없음
# ---------------------------------------------------------------------------
class TestMdToHtml:
    def _fn(self):
        from pipeline.tools_google import _md_to_html
        return _md_to_html

    def test_h1_converted(self):
        result = self._fn()("# 제목")
        assert "<h1>제목</h1>" in result

    def test_h2_converted(self):
        result = self._fn()("## 소제목")
        assert "<h2>소제목</h2>" in result

    def test_h3_converted(self):
        result = self._fn()("### 세제목")
        assert "<h3>세제목</h3>" in result

    def test_horizontal_rule(self):
        result = self._fn()("---")
        assert "<hr>" in result

    def test_list_item(self):
        result = self._fn()("- 항목 A")
        assert "<li" in result
        assert "항목 A" in result

    def test_bold_inline(self):
        result = self._fn()("**강조**")
        assert "<strong>강조</strong>" in result

    def test_italic_inline(self):
        result = self._fn()("*기울임*")
        assert "<em>기울임</em>" in result

    def test_code_inline(self):
        result = self._fn()("`코드`")
        assert "<code" in result
        assert "코드" in result

    def test_blank_line_becomes_br(self):
        result = self._fn()("\n")
        assert "<br>" in result

    def test_table_rendering(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = self._fn()(md)
        assert "<table" in result
        assert "<th" in result
        assert "<td" in result


# ---------------------------------------------------------------------------
# gmail_list_attachments  (L151-175)
# ---------------------------------------------------------------------------
class TestGmailListAttachments:
    def test_no_attachments_returns_empty(self):
        detail = {
            "payload": {
                "mimeType": "text/plain",
                "body": {},
                "parts": [],
            }
        }
        with patch("pipeline.tools_google._gapi", return_value=detail):
            from pipeline.tools_google import gmail_list_attachments
            result = gmail_list_attachments("m1")
        assert result == []

    def test_attachment_returned(self):
        detail = {
            "payload": {
                "mimeType": "multipart/mixed",
                "body": {},
                "parts": [
                    {
                        "filename": "report.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att123", "size": 4096},
                        "parts": [],
                    }
                ],
            }
        }
        with patch("pipeline.tools_google._gapi", return_value=detail):
            from pipeline.tools_google import gmail_list_attachments
            result = gmail_list_attachments("m1")
        assert len(result) == 1
        assert result[0]["filename"] == "report.pdf"
        assert result[0]["attachment_id"] == "att123"

    def test_nested_parts_walked(self):
        detail = {
            "payload": {
                "mimeType": "multipart/mixed",
                "body": {},
                "parts": [
                    {
                        "filename": "",
                        "body": {},
                        "mimeType": "multipart/related",
                        "parts": [
                            {
                                "filename": "image.png",
                                "mimeType": "image/png",
                                "body": {"attachmentId": "att_img", "size": 1024},
                                "parts": [],
                            }
                        ],
                    }
                ],
            }
        }
        with patch("pipeline.tools_google._gapi", return_value=detail):
            from pipeline.tools_google import gmail_list_attachments
            result = gmail_list_attachments("m1")
        assert len(result) == 1
        assert result[0]["filename"] == "image.png"


# ---------------------------------------------------------------------------
# drive_search  (L487-516)
# ---------------------------------------------------------------------------
class TestDriveSearch:
    def test_no_files_returns_empty(self):
        with patch("pipeline.tools_google._gapi", return_value={"files": []}):
            from pipeline.tools_google import drive_search
            result = drive_search("report")
        assert result == []

    def test_plain_query_wrapped_in_fulltext(self):
        captured = []

        def fake_gapi(path, account="", params=None, method="GET", body=None):
            if params:
                captured.append(params.get("q", ""))
            return {"files": []}

        with patch("pipeline.tools_google._gapi", side_effect=fake_gapi):
            from pipeline.tools_google import drive_search
            drive_search("my document")

        assert captured and "fullText contains" in captured[0]

    def test_drive_syntax_not_wrapped(self):
        captured = []

        def fake_gapi(path, account="", params=None, method="GET", body=None):
            if params:
                captured.append(params.get("q", ""))
            return {"files": []}

        with patch("pipeline.tools_google._gapi", side_effect=fake_gapi):
            from pipeline.tools_google import drive_search
            drive_search("name = 'report.pdf' and trashed = false")

        assert captured and "fullText contains" not in captured[0]

    def test_returns_file_fields(self):
        fake_resp = {
            "files": [{
                "id": "file1", "name": "report.pdf",
                "mimeType": "application/pdf",
                "modifiedTime": "2026-06-01T00:00:00Z",
                "webViewLink": "https://drive.google.com/file/d/file1",
            }]
        }
        with patch("pipeline.tools_google._gapi", return_value=fake_resp):
            from pipeline.tools_google import drive_search
            result = drive_search("report")
        assert result[0]["name"] == "report.pdf"
        assert result[0]["id"] == "file1"


# ---------------------------------------------------------------------------
# icloud functions  (L959-1018) — 순수 파일시스템, 외부 의존 없음
# ---------------------------------------------------------------------------
class TestIcloudFunctions:
    def test_resolve_icloud_path_prefix(self, tmp_path, monkeypatch):
        import pipeline.tools_google as tg
        monkeypatch.setattr(tg, "_ICLOUD_ROOT", tmp_path)
        from pipeline.tools_google import _resolve_icloud_path
        result = _resolve_icloud_path("~/iCloud/Documents/test.txt")
        assert "Documents/test.txt" in str(result)

    def test_icloud_list_missing_path(self, tmp_path, monkeypatch):
        import pipeline.tools_google as tg
        monkeypatch.setattr(tg, "_ICLOUD_ROOT", tmp_path)
        from pipeline.tools_google import icloud_list
        result = icloud_list("nonexistent_folder")
        assert result[0].get("error") is not None

    def test_icloud_list_existing(self, tmp_path, monkeypatch):
        import pipeline.tools_google as tg
        monkeypatch.setattr(tg, "_ICLOUD_ROOT", tmp_path)
        (tmp_path / "file.txt").write_text("hi")
        (tmp_path / "subdir").mkdir()
        from pipeline.tools_google import icloud_list
        result = icloud_list()
        names = [r["name"] for r in result]
        assert "file.txt" in names
        assert "subdir" in names

    def test_icloud_move_missing_src(self, tmp_path, monkeypatch):
        import pipeline.tools_google as tg
        monkeypatch.setattr(tg, "_ICLOUD_ROOT", tmp_path)
        from pipeline.tools_google import icloud_move
        result = icloud_move("missing.txt", "dest.txt")
        assert result["ok"] is False

    def test_icloud_move_success(self, tmp_path, monkeypatch):
        import pipeline.tools_google as tg
        monkeypatch.setattr(tg, "_ICLOUD_ROOT", tmp_path)
        src = tmp_path / "a.txt"
        src.write_text("content")
        from pipeline.tools_google import icloud_move
        result = icloud_move(str(src), str(tmp_path / "b.txt"))
        assert result["ok"] is True
        assert (tmp_path / "b.txt").exists()

    def test_icloud_rename_success(self, tmp_path, monkeypatch):
        import pipeline.tools_google as tg
        monkeypatch.setattr(tg, "_ICLOUD_ROOT", tmp_path)
        f = tmp_path / "old.txt"
        f.write_text("data")
        from pipeline.tools_google import icloud_rename
        result = icloud_rename(str(f), "new.txt")
        assert result["ok"] is True
        assert (tmp_path / "new.txt").exists()

    def test_icloud_rename_path_separator_rejected(self, tmp_path, monkeypatch):
        import pipeline.tools_google as tg
        monkeypatch.setattr(tg, "_ICLOUD_ROOT", tmp_path)
        f = tmp_path / "f.txt"
        f.write_text("x")
        from pipeline.tools_google import icloud_rename
        result = icloud_rename(str(f), "sub/new.txt")
        assert result["ok"] is False

    def test_icloud_mkdir_creates(self, tmp_path, monkeypatch):
        import pipeline.tools_google as tg
        monkeypatch.setattr(tg, "_ICLOUD_ROOT", tmp_path)
        new_dir = tmp_path / "newdir"
        from pipeline.tools_google import icloud_mkdir
        result = icloud_mkdir(str(new_dir))
        assert result["ok"] is True
        assert new_dir.is_dir()

    def test_icloud_mkdir_existing_fails(self, tmp_path, monkeypatch):
        import pipeline.tools_google as tg
        monkeypatch.setattr(tg, "_ICLOUD_ROOT", tmp_path)
        existing = tmp_path / "existing"
        existing.mkdir()
        from pipeline.tools_google import icloud_mkdir
        result = icloud_mkdir(str(existing))
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# calendar_create_event  (L423-447)
# ---------------------------------------------------------------------------
class TestCalendarCreateEvent:
    def test_creates_event_with_id(self):
        fake_resp = {"id": "evt1", "summary": "테스트 이벤트", "htmlLink": "https://cal.google.com/evt1"}
        with patch("pipeline.tools_google._gapi", return_value=fake_resp):
            from pipeline.tools_google import calendar_create_event
            result = calendar_create_event(
                summary="테스트 이벤트",
                start_iso="2026-06-20T10:00:00+09:00",
                end_iso="2026-06-20T11:00:00+09:00",
            )
        assert result["id"] == "evt1"
        assert result["summary"] == "테스트 이벤트"

    def test_description_and_location_sent(self):
        captured_body = []

        def fake_gapi(path, account="", params=None, method="GET", body=None):
            captured_body.append(body)
            return {"id": "e2"}

        with patch("pipeline.tools_google._gapi", side_effect=fake_gapi):
            from pipeline.tools_google import calendar_create_event
            calendar_create_event(
                summary="미팅",
                start_iso="2026-06-20T10:00:00+09:00",
                end_iso="2026-06-20T11:00:00+09:00",
                description="설명",
                location="서울",
            )

        body = captured_body[0]
        assert body["description"] == "설명"
        assert body["location"] == "서울"

    def test_empty_description_not_sent(self):
        captured_body = []

        def fake_gapi(path, account="", params=None, method="GET", body=None):
            captured_body.append(body)
            return {}

        with patch("pipeline.tools_google._gapi", side_effect=fake_gapi):
            from pipeline.tools_google import calendar_create_event
            calendar_create_event("미팅", "2026-06-20T10:00:00+09:00", "2026-06-20T11:00:00+09:00")

        body = captured_body[0]
        assert "description" not in body
