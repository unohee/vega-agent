# Created: 2026-06-08
# Purpose: chat.html stick-to-bottom 자동 스크롤 회귀 테스트(헤드리스 Chrome).
#          응답 생성 중 위로 올려 읽으면 자동 스크롤 멈춤, 바닥 복귀 시 재개, force는 무조건 바닥.
# Dependencies: Google Chrome (headless), web/static/chat.html, tests/js/scroll_harness.html
# Test Status: 검증 중

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path

import pytest

_HARNESS = Path(__file__).parent / "js" / "scroll_harness.html"
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


@pytest.mark.skipif(_find_chrome() is None, reason="Chrome 미설치 — 스크롤 UI 테스트 건너뜀")
def test_stick_to_bottom_scroll():
    """응답 생성 중 자동 스크롤이 사용자 읽기를 방해하지 않는지(INT-1397).
    회귀: 토큰마다 무조건 바닥으로 끌어내려 위로 못 읽던 동작을 막는다."""
    assert _HARNESS.exists(), f"하니스 없음: {_HARNESS}"
    chrome = _find_chrome()
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            chrome, "--headless", "--disable-gpu", "--no-sandbox",
            "--allow-file-access-from-files", "--virtual-time-budget=6000",
            f"--user-data-dir={tmp}", "--dump-dom", f"file://{_HARNESS.resolve()}",
        ]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, start_new_session=True)
        try:
            dom, _ = p.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()
            dom, _ = p.communicate()
    m = re.search(r"RESULT_JSON:(\{.*?\})</div>", dom, re.DOTALL)
    assert m, f"결과 추출 실패. DOM 일부:\n{dom[:1500]}"
    result = json.loads(m.group(1))
    assert result.get("ok") is True, (
        f"스크롤 케이스 실패: {json.dumps(result, ensure_ascii=False)}"
    )
    # 개별 동작 명시 검증
    assert result["follows_when_at_bottom"], "바닥에 있을 때 새 콘텐츠를 따라가지 않음"
    assert result["stick_off_after_scroll_up"], "위로 스크롤 후에도 자동 스크롤이 안 멈춤"
    assert result["holds_position_when_scrolled_up"], "위로 읽는 중 새 콘텐츠가 위치를 뺏음(핵심 버그)"
    assert result["stick_on_after_return"], "바닥 복귀 후 자동 스크롤이 재개되지 않음"
    assert result["force_scrolls_to_bottom"], "force 스크롤(내 메시지)이 바닥으로 안 감"
