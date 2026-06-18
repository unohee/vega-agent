# Created: 2026-06-10
# Purpose: chat.html 스트리밍 타이퍼 성능·정확성 회귀 테스트 (헤드리스 Chrome, INT-1430).
#          블록 델타 파싱(O(n)) / catch-up / 최종 상태 일치 / breakSegment / 코드펜스 경계.
# Dependencies: Google Chrome (headless), web/static/chat.html, tests/js/typer_harness.html
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

_HARNESS = Path(__file__).parent / "js" / "typer_harness.html"
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


@pytest.mark.skipif(_find_chrome() is None, reason="Chrome 미설치 — 타이퍼 UI 테스트 건너뜀")
def test_typer_streaming_performance_and_correctness():
    """저사양 끊김 원인이던 타이퍼 O(n²) 재파싱·초당 167자 상한의 회귀를 막는다(INT-1430).
    - parse_total_linear: 누적 텍스트 전체 재파싱으로 돌아가면 빨간불
    - catchup_fast: CHUNK_SIZE 고정 소비로 돌아가면 빨간불
    - final/break/fence: 점진 렌더가 최종 상태·세그먼트·코드펜스를 깨면 빨간불"""
    assert _HARNESS.exists(), f"하니스 없음: {_HARNESS}"
    chrome = _find_chrome()
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            chrome, "--headless", "--disable-gpu", "--no-sandbox",
            "--allow-file-access-from-files", "--virtual-time-budget=20000",
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
    assert result.get("ok") is True, f"타이퍼 케이스 실패: {json.dumps(result, ensure_ascii=False)}"
    assert result["parse_total_linear"], (
        f"파싱 총량 비선형 — O(n²) 회귀 의심 "
        f"(parsed={result.get('parse_total_chars')}, n={result.get('text_len')})"
    )
    assert result["catchup_fast"], f"catch-up 미동작 — 표시 지연 {result.get('elapsed_ms')}ms"
    assert result["final_state_exact"], "최종 상태가 전체 1회 파싱과 다름"
    assert result["break_first_closed"], "breakSegment 후 첫 세그먼트 미확정"
    assert result["fence_open_not_balanced"], "열린 코드펜스를 stable 경계로 오인"
    # INT-1564: done 시 usage-meta를 typer flush 콜백 후로 미뤄 본문 중복(메타 뒤 재렌더)을 막는다.
    assert result["dup_single_segment"], "done 후 본문 세그먼트가 1개 초과 — 중복 렌더 회귀(INT-1564)"
    assert result["dup_body_once"], "본문이 2회 렌더됨 — usage-meta flush 순서 회귀(INT-1564)"
    assert result["dup_meta_after_body"], "usage-meta가 본문 세그먼트 뒤에 위치하지 않음(INT-1564)"
