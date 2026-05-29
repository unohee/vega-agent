# Created: 2026-05-20
# Purpose: Web search + page fetch tools (split from tools.py)
# Dependencies: stdlib, playwright

from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request

# SearXNG endpoint. Deployment scenarios:
#   - Single user: localhost:18888 (local Docker)
#   - Internal deployment: set VEGA_SEARXNG_URL=http://intranet-searxng.local:8080
# Injected via .env or LaunchAgent EnvironmentVariables.
SEARXNG_URL = os.environ.get("VEGA_SEARXNG_URL", "http://localhost:18888").rstrip("/")


def _pw_get_text(page) -> str:
    for sel in ["article", "main", "[role='main']", ".content", "#content", "body"]:
        try:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text()
                if len(text) > 200:
                    return re.sub(r"\n{3,}", "\n\n", text).strip()
        except Exception:
            pass
    return page.inner_text("body")


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """SearXNG meta-search using the instance at SEARXNG_URL.

    Raises RuntimeError on failure — caught by dispatch_tool's try/except and
    converted to {"error": "..."}, consistent with telemetry/self_improve convention.
    Returning a raw list (not dict) caused failures to be counted as successes."""
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "language": "ko-KR",
        "categories": "general",
    })
    req = urllib.request.Request(
        f"{SEARXNG_URL}/search?{params}",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            import json
            data = json.loads(r.read())
    except Exception as e:
        # Covers: SearXNG not running, network error, JSON parse failure
        raise RuntimeError(f"SearXNG ({SEARXNG_URL}) request failed: {e}") from e

    results = []
    for item in data.get("results", [])[:max_results]:
        raw_content = item.get("content", "")[:400]
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            # Boundary markers to prevent the LLM from confusing external content with internal instructions
            "content": f"[외부 콘텐츠 시작]\n{raw_content}\n[외부 콘텐츠 끝]",
        })
    return results


def web_fetch(url: str, timeout: int = 20000) -> str:
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            text = _pw_get_text(page)
            browser.close()
        # Boundary markers to prevent the LLM from confusing external content with internal instructions
        return f"[외부 URL: {url}]\n[콘텐츠 시작]\n{text[:6000]}\n[콘텐츠 끝]"
    except Exception as e:
        return f"fetch 실패: {e}"
