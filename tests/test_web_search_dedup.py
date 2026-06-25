# Created: 2026-06-24
# Purpose: duplicate web_search guard (INT-1893 Phase 3).

from __future__ import annotations

import pytest

from pipeline.tools_web import clear_web_search_cache, web_search


def test_web_search_blocks_duplicate_query(monkeypatch):
    clear_web_search_cache()

    def fake_urlopen(req, timeout=15):
        class R:
            def read(self):
                return b'{"results": [{"title": "a", "url": "http://x", "content": "c"}]}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    web_search("ikea lamp", max_results=1)
    with pytest.raises(RuntimeError, match="동일 검색어"):
        web_search("ikea lamp", max_results=1)
