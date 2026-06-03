# Created: 2026-06-03
# Purpose: chat.html 인터리빙(텍스트↔도구 시간순 배치) 회귀 테스트를 pytest로 통합.
#          실제 검증은 node 러너(tests/js/interleave_runner.js)가 수행 — chat.html에서
#          함수를 추출해 jsdom-lite 위에서 SSE 시퀀스를 시뮬레이션.
# Dependencies: node (PATH), web/static/chat.html
# Test Status: 검증 중

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_RUNNER = Path(__file__).parent / "js" / "interleave_runner.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치 — JS 인터리빙 테스트 건너뜀")
def test_chat_interleave_dom_sequence():
    """도구 호출과 본문 텍스트가 시간 순서대로 교차 배치되는지 검증.
    회귀: 과거엔 도구 배지를 버블 맨 위에 모으고(insertBefore firstChild) 텍스트를
    아래 한 덩어리로 쌓아 시간순이 사라졌다. 인터리빙 도입 후 순서가 보존돼야 한다."""
    assert _RUNNER.exists(), f"러너 없음: {_RUNNER}"
    r = subprocess.run(
        ["node", str(_RUNNER)],
        capture_output=True, text=True, timeout=30,
    )
    # 실패 시 러너의 stdout(어떤 시나리오가 깨졌는지)을 그대로 노출
    assert r.returncode == 0, f"인터리빙 시나리오 실패:\n{r.stdout}\n{r.stderr}"
    # 4개 시나리오가 모두 ✓ 인지 확인
    assert r.stdout.count("✓") == 4, f"통과 시나리오 부족:\n{r.stdout}"
