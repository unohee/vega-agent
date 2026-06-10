# Created: 2026-06-10
# Purpose: chat.html 자동 업데이트 배너 회귀 테스트 (헤드리스 Chrome, INT-1434).
#          update-ready 이벤트 → 비방해적 배너 표시 / 버전 렌더 / 멱등 / dismiss.
# Dependencies: Google Chrome (headless), web/static/chat.html, tests/js/update_banner_harness.html
# Test Status: green (2026-06-10)

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path

import pytest

_HARNESS = Path(__file__).parent / "js" / "update_banner_harness.html"
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


@pytest.mark.skipif(_find_chrome() is None, reason="Chrome 미설치 — 업데이트 배너 UI 테스트 건너뜀")
def test_update_ready_banner():
    """자동 업데이트가 강제 재시작 없이 배너로 안내되는지(INT-1434).
    회귀: lib.rs가 download_and_install 후 app.restart()로 강제 재시작하던 동작을
    '설치만 + update-ready emit + 다음 실행 시 적용'으로 바꿨고, 프론트가 그 이벤트를
    비방해적 배너로 받는다. 배너 미표시/중복/버전 누락 시 빨간불."""
    assert _HARNESS.exists(), f"하니스 없음: {_HARNESS}"
    chrome = _find_chrome()
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            chrome, "--headless", "--disable-gpu", "--no-sandbox",
            "--allow-file-access-from-files", "--virtual-time-budget=4000",
            f"--user-data-dir={tmp}", "--dump-dom", f"file://{_HARNESS.resolve()}",
        ]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, start_new_session=True)
        try:
            dom, _ = p.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()
            dom, _ = p.communicate()
    m = re.search(r"RESULT_JSON:(\{.*?\})</div>", dom, re.DOTALL)
    assert m, f"결과 추출 실패. DOM 일부:\n{dom[:1500]}"
    result = json.loads(m.group(1))
    assert result.get("ok") is True, f"배너 케이스 실패: {json.dumps(result, ensure_ascii=False)}"
    assert result["banner_appears"], "update-ready 이벤트에 배너가 안 뜸"
    assert result["shows_version"], "배너에 버전(0.1.12) 미표시"
    assert result["idempotent_single"], "중복 emit에 배너가 여러 개 생김(멱등 깨짐)"
    assert result["dismiss_removes"], "확인 버튼으로 배너가 안 닫힘"
