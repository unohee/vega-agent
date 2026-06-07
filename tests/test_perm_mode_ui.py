# Created: 2026-06-06
# Purpose: chat.html permission 모드 드롭다운 UI 회귀 테스트(헤드리스 Chrome).
#          Claude Code 스타일 순환(default→plan→bypass), 배지 동기화, research 독립.
# Dependencies: Google Chrome (headless), web/static/chat.html, tests/js/perm_mode_harness.html
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

_HARNESS = Path(__file__).parent / "js" / "perm_mode_harness.html"
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


@pytest.mark.skipif(_find_chrome() is None, reason="Chrome 미설치 — permission UI 테스트 건너뜀")
def test_permission_mode_ui():
    """permission 드롭다운이 Claude Code 스타일로 순환·동기화하는지.
    회귀: 순환 순서가 깨지거나, plan↔bypass 배지가 안 바뀌거나, research가
    permission과 섞이는 것을 막는다."""
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
    m = re.search(r"RESULT_JSON:(\{.*?\})</pre>", dom, re.DOTALL)
    assert m, f"결과 추출 실패. DOM 일부:\n{dom[:1500]}"
    result = json.loads(m.group(1))
    failed = [r for r in result.get("results", []) if not r.get("pass")]
    assert result.get("ok") is True, (
        f"permission UI 케이스 실패: {failed}\n전체: {json.dumps(result, ensure_ascii=False)}"
    )
    cases = {r["case"] for r in result["results"]}
    assert cases == {"cycle→plan", "cycle→bypass", "cycle→default", "set bypass", "research독립", "메뉴체크"}, \
        f"케이스 누락: {cases}"
