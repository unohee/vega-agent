# Created: 2026-06-05
# Purpose: chat.html 진행표시 워치독 회귀 테스트. streaming 중 이벤트 사이 idle
#          구간(도구 끝~답변 전, 토큰 끊김, 도구 실행 중, 재접속, 종료 후)에
#          "생각 중" 표시 연속성이 올바른지 헤드리스 Chrome 실제 DOM+타이머로 검증.
# Dependencies: Google Chrome (headless), web/static/chat.html, tests/js/progress_watch_harness.html
# Test Status: 검증 중

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

_HARNESS = Path(__file__).parent / "js" / "progress_watch_harness.html"

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


@pytest.mark.skipif(_find_chrome() is None, reason="Chrome 미설치 — 워치독 DOM 테스트 건너뜀")
def test_progress_watch_continuity():
    """진행표시 워치독 불변식: streaming 중에는 회전 도구 배지 / thinking 배지 중
    하나가 항상 살아있어야 한다. 5개 케이스를 실제 브라우저에서 검증.

    회귀: 과거엔 이벤트 단위로만 thinking을 켜고 꺼서, 도구 완료~다음 토큰 사이,
    토큰 끊김, 재접속 직후 등 idle 구간에 표시가 사라져 '멈춘 듯' 보였다."""
    assert _HARNESS.exists(), f"하니스 없음: {_HARNESS}"
    chrome = _find_chrome()

    # 주의: --dump-dom은 DOM을 stdout에 찍은 뒤에도 Chrome 프로세스가 백그라운드
    # 업데이터/crashpad 때문에 종료되지 않아 명령이 hang한다. DOM은 이미 회수됐으므로
    # timeout으로 끊고 그 시점의 stdout(부분 출력 = 완성된 DOM)을 파싱한다.
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            chrome, "--headless", "--disable-gpu", "--no-sandbox",
            "--allow-file-access-from-files",
            "--virtual-time-budget=8000",
            f"--user-data-dir={tmp}",
            "--dump-dom", f"file://{_HARNESS.resolve()}",
        ]
        # 프로세스 그룹으로 띄워 timeout 시 렌더러 자식까지 그룹째 종료(좀비 방지).
        import os
        import signal
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
            dom, _ = p.communicate()  # DOM은 이미 찍혔으므로 부분 출력 회수
    # result-sink <pre>에 'RESULT_JSON:{...}' 형태로 결과가 박혀 있다.
    # (소스의 문자열 리터럴에도 'RESULT_JSON:' 가 있으므로 '{' 로 시작하는 실제 결과만 매칭)
    m = re.search(r"RESULT_JSON:(\{.*?\})</pre>", dom, re.DOTALL)
    assert m, f"결과 추출 실패. DOM 일부:\n{dom[:1500]}"
    result = json.loads(m.group(1))

    failed = [r for r in result.get("results", []) if not r.get("pass")]
    assert result.get("ok") is True, (
        f"워치독 케이스 실패: {failed}\n전체: {json.dumps(result, ensure_ascii=False)}"
    )
    # 5개 케이스 모두 존재 확인 (시나리오 누락 방지)
    cases = {r["case"] for r in result["results"]}
    assert cases == {"도구갭", "도구실행중", "토큰끊김", "종료후무생성", "승인대기"}, \
        f"케이스 누락/변경: {cases}"
