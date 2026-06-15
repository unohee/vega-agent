# Created: 2026-05-20
# Purpose: Web search + page fetch tools (split from tools.py)
# Dependencies: stdlib, playwright

from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request

# SearXNG endpoint. 기본값 = 호스팅 인스턴스 search.intrect.io — 배포 사용자가
# 로컬 Docker SearXNG 없이도 web_search가 동작한다 (No setup tax).
# 단, 인스턴스가 X-VEGA-Key를 요구하므로 VEGA_SEARXNG_KEY(또는 VEGA_API_KEY) 필요 —
# 미설정 시 401을 키 안내 에러로 변환한다.
# Resolution priority (read at call time so GUI saves apply immediately):
#   Keychain > .env/env var > default. The settings window (Tools & Keys) saves to Keychain.
#   (로컬 SearXNG를 쓰려면 VEGA_SEARXNG_URL=http://localhost:18888 설정)  # cxt-ignore: fake_data
_DEFAULT_SEARXNG_URL = "https://search.intrect.io"


# keychain.get 체인(Keychain → .env → 환경변수)을 쓴다 — get_secret(Keychain 단독)이
# 아니라 체인을 타야 배포본에 동봉된 번들 .env(_MEIPASS/.env)의 기본 키가 보인다.

def _get_searxng_url() -> str:
    try:
        from pipeline.keychain import get as kc_get
        v = kc_get("VEGA_SEARXNG_URL")
        if v:
            return v.rstrip("/")
    except Exception:
        pass
    return os.environ.get("VEGA_SEARXNG_URL", _DEFAULT_SEARXNG_URL).rstrip("/")


def _get_searxng_key() -> str:
    try:
        from pipeline.keychain import get as kc_get
        v = kc_get("VEGA_SEARXNG_KEY") or kc_get("VEGA_API_KEY")
        if v:
            return v
    except Exception:
        pass
    return os.environ.get("VEGA_SEARXNG_KEY", "") or os.environ.get("VEGA_API_KEY", "")


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
    headers = {
        "Accept": "application/json",
        "User-Agent": "VEGA/1.0",
    }
    searxng_url = _get_searxng_url()
    searxng_key = _get_searxng_key()
    if searxng_key:
        headers["X-VEGA-Key"] = searxng_key
    req = urllib.request.Request(
        f"{searxng_url}/search?{params}",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            import json
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            # 호스팅 게이트웨이는 키 필수 — 막연한 실패 대신 해결 경로를 알려준다
            raise RuntimeError(
                f"검색 게이트웨이({searxng_url}) 인증 실패({e.code}) — "
                "설정 → Tools & Keys에서 VEGA_SEARXNG_KEY를 등록해라."
            ) from e
        raise RuntimeError(f"SearXNG ({searxng_url}) request failed: {e}") from e
    except Exception as e:
        # Covers: SearXNG not running, network error, JSON parse failure
        raise RuntimeError(f"SearXNG ({searxng_url}) request failed: {e}") from e

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


# 정적 fetch가 이 길이 이상의 본문을 추출하면 Chromium 폴백을 생략한다
# (_pw_get_text의 본문 판정 기준 200자와 동일)
_MIN_STATIC_TEXT = 200

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _strip_html(html: str) -> str:
    """서버 렌더링된 HTML에서 본문 텍스트 추출 (의존성 없는 정규식 기반)."""
    import html as _html_mod
    html = re.sub(r"(?is)<(script|style|noscript|svg|head|template)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>|</(p|div|li|tr|h[1-6]|blockquote|pre)>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = _html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch_static(url: str, timeout_s: float) -> str:
    """httpx로 정적 HTML을 받아 본문 추출. 실패/비-텍스트면 예외."""
    import httpx
    r = httpx.get(url, headers={"User-Agent": _UA}, follow_redirects=True, timeout=timeout_s)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        raise RuntimeError(f"non-text content-type: {ctype}")
    return _strip_html(r.text)


def _fetch_browser(url: str, timeout: int) -> str:
    """Chromium(playwright)으로 렌더링 후 본문 추출 — JS 렌더 페이지 폴백."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=_UA)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        text = _pw_get_text(page)
        browser.close()
    return text


def _ssrf_guard(url: str) -> None:
    """SSRF 방지: RFC1918/loopback/링크로컬/메타데이터 서비스 URL을 차단한다."""
    import ipaddress
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"허용되지 않는 스킴: {scheme}")
    host = parsed.hostname or ""
    # 인스턴스 메타데이터 서비스 호스트명 차단
    _BLOCKED_HOSTS = {"metadata.google.internal", "169.254.169.254"}
    if host.lower() in _BLOCKED_HOSTS:
        raise ValueError(f"내부 메타데이터 호스트 접근 차단: {host}")
    # IP 리터럴이면 프라이빗/링크로컬/루프백 차단
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise ValueError(f"내부 IP 접근 차단: {host}")
    except ValueError as e:
        if "내부" in str(e):
            raise
        pass  # 호스트명은 IP 파싱 실패 → 통과(DNS 해석 시점 차단은 불가)


def web_fetch(url: str, timeout: int = 20000) -> str:
    _ssrf_guard(url)
    # 1차: httpx 정적 fetch — 호출마다 Chromium을 새로 띄우는 비용(1-3초 +
    # 수백 MB 메모리 스파이크, 저사양 Mac 체감 끊김 INT-1430)을 대부분의
    # 서버 렌더링 페이지에서 생략한다. 본문이 부족하면(JS 렌더) Chromium 폴백.
    text = ""
    try:
        text = _fetch_static(url, timeout / 1000)
    except Exception:
        pass
    if len(text) < _MIN_STATIC_TEXT:
        try:
            text = _fetch_browser(url, timeout)
        except Exception as e:
            if not text:
                return f"fetch 실패: {e}"
            # 짧더라도 정적 결과가 있으면 그걸 반환
    # Boundary markers to prevent the LLM from confusing external content with internal instructions
    return f"[외부 URL: {url}]\n[콘텐츠 시작]\n{text[:6000]}\n[콘텐츠 끝]"
