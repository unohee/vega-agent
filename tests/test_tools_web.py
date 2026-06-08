# Created: 2026-05-26
# Purpose: pipeline/tools_web.py 단위 테스트 — 웹 검색 + fetch 함수
# Dependencies: pipeline/tools_web.py
# Test Status: 신규

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from pipeline.tools_web import (
    _get_searxng_url,
    _pw_get_text,
    web_fetch,
    web_search,
)


class TestWebSearch:
    """웹 검색 함수 테스트 — SearXNG API mock."""

    def test_searxng_url_resolution(self, monkeypatch):
        """런타임 URL 해석: Keychain > env > 기본값."""
        monkeypatch.setattr("pipeline.keychain.get_secret", lambda *a, **k: None)
        monkeypatch.delenv("VEGA_SEARXNG_URL", raising=False)
        assert _get_searxng_url() == "http://localhost:18888"
        monkeypatch.setenv("VEGA_SEARXNG_URL", "http://envhost:9/")
        assert _get_searxng_url() == "http://envhost:9"
        monkeypatch.setattr("pipeline.keychain.get_secret",
                            lambda key, **k: "https://kc.example.com/" if key == "VEGA_SEARXNG_URL" else None)
        assert _get_searxng_url() == "https://kc.example.com"

    def test_web_search_success(self):
        """SearXNG 검색 성공."""
        response_data = {
            "results": [
                {
                    "title": "Python 튜토리얼",
                    "url": "https://python.org/docs",
                    "content": "Python 프로그래밍 언어 공식 문서",
                },
                {
                    "title": "Python 커뮤니티",
                    "url": "https://python.org/community",
                    "content": "Python 커뮤니티 및 이벤트",
                },
            ]
        }

        with patch("pipeline.tools_web.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(response_data).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            results = web_search("Python 튜토리얼", max_results=5)
            assert len(results) == 2
            assert results[0]["title"] == "Python 튜토리얼"
            assert "python.org" in results[0]["url"]

    def test_web_search_no_results(self):
        """검색 결과 없음."""
        response_data = {"results": []}

        with patch("pipeline.tools_web.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(response_data).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            results = web_search("비존재 검색어")
            assert len(results) == 0

    def test_web_search_respects_max_results(self):
        """max_results 제한 준수."""
        response_data = {
            "results": [
                {"title": f"결과 {i}", "url": f"https://example.com/{i}", "content": f"내용 {i}"}
                for i in range(10)
            ]
        }

        with patch("pipeline.tools_web.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(response_data).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            results = web_search("테스트", max_results=3)
            assert len(results) == 3

    def test_web_search_content_truncated(self):
        """raw content는 400자로 자르기 (래퍼 문자열 제외)."""
        long_content = "a" * 1000
        response_data = {
            "results": [
                {
                    "title": "긴 내용 페이지",
                    "url": "https://example.com",
                    "content": long_content,
                }
            ]
        }

        with patch("pipeline.tools_web.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(response_data).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            results = web_search("테스트")
            content = results[0]["content"]
            # 래퍼([외부 콘텐츠 시작/끝]) 제외한 raw 부분이 400자 이하인지 검증
            raw = content.replace("[외부 콘텐츠 시작]\n", "").replace("\n[외부 콘텐츠 끝]", "")
            assert len(raw) <= 400

    def test_web_search_api_error(self):
        """SearXNG API 연결 실패 시 RuntimeError raise (dispatch_tool이 {"error":...}로 변환)."""
        import pytest
        with patch("pipeline.tools_web.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("연결 실패")

            with pytest.raises(RuntimeError, match="SearXNG"):
                web_search("테스트")

    def test_web_search_timeout(self):
        """검색 타임아웃 시 RuntimeError raise."""
        import pytest
        with patch("pipeline.tools_web.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError("타임아웃")

            with pytest.raises(RuntimeError, match="SearXNG"):
                web_search("테스트")


class TestPwGetText:
    """Playwright 페이지 텍스트 추출 함수."""

    def test_pw_get_text_article_selector(self):
        """article 선택자로 텍스트 추출."""
        mock_page = MagicMock()
        mock_article = MagicMock()
        mock_article.inner_text.return_value = "a" * 300
        mock_page.query_selector.side_effect = [mock_article, None, None, None, None, None]

        text = _pw_get_text(mock_page)
        assert text == "a" * 300
        assert mock_page.query_selector.call_count >= 1

    def test_pw_get_text_fallback_to_body(self):
        """모든 선택자 실패 시 body로 폴백."""
        mock_page = MagicMock()
        mock_page.query_selector.return_value = None
        mock_page.inner_text.return_value = "body 텍스트"

        text = _pw_get_text(mock_page)
        assert text == "body 텍스트"

    def test_pw_get_text_removes_extra_newlines(self):
        """3줄 이상 연속 개행 제거."""
        mock_page = MagicMock()
        mock_article = MagicMock()
        content = "line1\n\n\n\nline2" + "x" * 300  # 200자 이상
        mock_article.inner_text.return_value = content
        mock_page.query_selector.return_value = mock_article

        text = _pw_get_text(mock_page)
        assert "\n\n\n" not in text
        assert "line1" in text and "line2" in text

    def test_pw_get_text_skips_short_content(self):
        """200자 미만 내용은 다음 선택자 시도."""
        mock_page = MagicMock()
        mock_short = MagicMock()
        mock_short.inner_text.return_value = "짧음"
        mock_long = MagicMock()
        mock_long.inner_text.return_value = "a" * 300
        mock_page.query_selector.side_effect = [mock_short, mock_long, None, None, None, None]

        text = _pw_get_text(mock_page)
        assert "a" * 300 in text
        assert "짧음" not in text


