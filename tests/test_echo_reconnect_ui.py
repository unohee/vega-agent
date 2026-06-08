# Created: 2026-06-08
# Purpose: chat.html 토큰 렌더러 재연결 멱등 회귀. 재연결 시 서버가 buf를 처음부터
#          재전송해도 본문이 중복(echo)되지 않아야 한다 — INT-1411. 헤드리스 Chrome 실제 DOM.
# Dependencies: Google Chrome (headless), web/static/chat.html, tests/js/echo_reconnect_harness.html

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path

import pytest

_HARNESS = Path(__file__).parent / "js" / "echo_reconnect_harness.html"

_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
    "chromium-browser",
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


@pytest.mark.skipif(_find_chrome() is None, reason="Chrome 미설치 — 렌더러 DOM 테스트 건너뜀")
def test_echo_reconnect_no_duplication():
    """재연결 멱등 불변식 (INT-1411): 재연결 시 버블 리셋 + 새 typer로,
    서버가 buf 전체를 재전송해도 본문이 한 번만 렌더된다(스트리밍 중 echo 방지).
    단일 스트림·도구 분리 케이스도 중복이 없어야 한다."""
    assert _HARNESS.exists(), f"하니스 없음: {_HARNESS}"
    chrome = _find_chrome()

    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            chrome, "--headless", "--disable-gpu", "--no-sandbox",
            "--allow-file-access-from-files",
            "--virtual-time-budget=8000",
            f"--user-data-dir={tmp}",
            "--dump-dom", f"file://{_HARNESS.resolve()}",
        ]
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True,
        )
        try:
            dom, _ = p.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()
            dom, _ = p.communicate()

    m = re.search(r"RESULT_JSON:(\{.*?\})</pre>", dom, re.DOTALL)
    assert m, f"결과 추출 실패. DOM 일부:\n{dom[:1500]}"
    result = json.loads(m.group(1))

    failed = [r for r in result.get("results", []) if not r.get("pass")]
    assert result.get("ok") is True, (
        f"렌더러 케이스 실패: {failed}\n전체: {json.dumps(result, ensure_ascii=False)}"
    )
    cases = {r["case"] for r in result["results"]}
    assert cases == {"재연결멱등", "단일스트림", "도구분리"}, f"케이스 누락/변경: {cases}"
