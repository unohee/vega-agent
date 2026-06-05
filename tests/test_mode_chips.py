# Created: 2026-06-06
# Purpose: chat.html 입력창 인라인 모드 칩(toggleMode/_syncModeChip) 회귀 테스트.
#          칩 클릭 → 올바른 모드 엔드포인트 POST → 서버 확정값으로 active 반영,
#          모드 간 독립성. 헤드리스 Chrome 실제 DOM + fetch mock.
# Dependencies: Google Chrome (headless), web/static/chat.html, tests/js/mode_chips_harness.html
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

_HARNESS = Path(__file__).parent / "js" / "mode_chips_harness.html"

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


def _run_harness(harness: Path) -> dict:
    chrome = _find_chrome()
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            chrome, "--headless", "--disable-gpu", "--no-sandbox",
            "--allow-file-access-from-files", "--virtual-time-budget=6000",
            f"--user-data-dir={tmp}", "--dump-dom", f"file://{harness.resolve()}",
        ]
        # --dump-dom은 DOM 출력 후에도 Chrome이 종료 안 돼 hang → timeout으로 끊고 부분출력 회수.
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
    return json.loads(m.group(1))


@pytest.mark.skipif(_find_chrome() is None, reason="Chrome 미설치 — 모드 칩 DOM 테스트 건너뜀")
def test_mode_chips_toggle():
    """입력창 모드 칩이 슬래시 커맨드 없이 on/off로 작동하는지 검증.
    회귀: 칩 클릭이 잘못된 엔드포인트를 치거나, 한 모드 토글이 다른 모드 칩에
    새거나, 서버 확정값 대신 낙관적 상태에 머무는 것을 막는다."""
    assert _HARNESS.exists(), f"하니스 없음: {_HARNESS}"
    result = _run_harness(_HARNESS)
    failed = [r for r in result.get("results", []) if not r.get("pass")]
    assert result.get("ok") is True, (
        f"칩 토글 케이스 실패: {failed}\n전체: {json.dumps(result, ensure_ascii=False)}"
    )
    cases = {r["case"] for r in result["results"]}
    assert cases == {"yolo켜기", "yolo끄기", "research독립", "plan경로"}, f"케이스 누락: {cases}"
