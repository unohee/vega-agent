# Created: 2026-06-23
# Purpose: chat.html 코드블록 구문 강조(highlightWithin)의 회귀 테스트 (헤드리스 Chrome, INT-1846).
#          실제 vendored highlight.js로 강조·자동감지·idempotency·null 안전을 검증.
# Dependencies: Google Chrome (headless), web/static/chat.html,
#               web/static/vendor/highlightjs/highlight.min.js, tests/js/highlight_harness.html
# Test Status: green (2026-06-23)

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path

import pytest

_HARNESS = Path(__file__).parent / "js" / "highlight_harness.html"
_HLJS = Path(__file__).parent.parent / "web" / "static" / "vendor" / "highlightjs" / "highlight.min.js"
_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
]


def _find_chrome() -> str | None:
    import shutil
    for c in _CHROME_CANDIDATES:
        if c.startswith("/"):
            if Path(c).exists():
                return c
        elif shutil.which(c):
            return shutil.which(c)
    return None


def test_vendored_highlightjs_present():
    """번들 누락이면 강조가 통째로 죽으므로 자산 존재를 먼저 못박는다."""
    assert _HLJS.exists(), f"vendored highlight.js 없음: {_HLJS}"
    assert _HLJS.stat().st_size > 50_000, "highlight.min.js 가 비정상적으로 작음(다운로드 손상 의심)"


@pytest.mark.skipif(_find_chrome() is None, reason="Chrome 미설치 — 구문강조 UI 테스트 건너뜀")
def test_code_block_syntax_highlighting():
    """marked가 낸 <pre><code>를 highlight.js가 후처리해 토큰 span을 만드는지 검증(INT-1846).
    - 언어 클래스 있는 블록: .hljs + hljs-* 토큰 span
    - 무클래스 블록: 자동 감지 강조
    - idempotency: 두 번 호출해도 중복/throw 없음(dataset.hl 가드)
    - null/빈 root 안전"""
    assert _HARNESS.exists(), f"하니스 없음: {_HARNESS}"
    chrome = _find_chrome()
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            chrome, "--headless", "--disable-gpu", "--no-sandbox",
            "--allow-file-access-from-files", "--virtual-time-budget=15000",
            f"--user-data-dir={tmp}", "--dump-dom", f"file://{_HARNESS.resolve()}",
        ]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, start_new_session=True)
        try:
            dom, _ = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()
            dom, _ = p.communicate()
    m = re.search(r"RESULT_JSON:(\{.*?\})</div>", dom, re.DOTALL)
    assert m, f"결과 추출 실패. DOM 일부:\n{dom[:1500]}"
    result = json.loads(m.group(1))
    assert result.get("ok") is True, f"구문강조 케이스 실패: {json.dumps(result, ensure_ascii=False)}"
    assert result["hljs_loaded"], "vendored highlight.js 로드 실패"
    assert result["py_hljs_class"] and result["py_has_token_span"], "언어 클래스 블록 강조 실패"
    assert result["auto_hljs_class"] and result["auto_has_token_span"], "무클래스 자동 감지 강조 실패"
    assert result["idempotent_no_throw"] and result["idempotent_stable"], "중복 강조 가드 회귀"
    assert result["empty_safe"], "null/빈 root에서 throw"
