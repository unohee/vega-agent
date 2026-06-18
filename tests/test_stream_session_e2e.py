# Created: 2026-06-18
# Purpose: INT-1566(응답 중단 알림) + INT-1565(세션 전환) E2E 회귀 — LLM 비의존.
#          page.route로 SSE를 done 없이 끊어 중단 안내를 검증하고, 세션 2개로
#          순수 클릭 전환(낙관적)을 검증한다. 실서버(localhost:8100) + playwright 필요.
# Dependencies: playwright(py), 가동 중인 VEGA 서버. 둘 중 하나라도 없으면 skip.
# Test Status: green (2026-06-18, INT-1569)

from __future__ import annotations

import json
import urllib.request

import pytest

# playwright 미설치 환경(대다수 CI)에선 모듈 수집 단계에서 skip
pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:8100"


def _server_up() -> bool:
    try:
        urllib.request.urlopen(BASE + "/api/sessions/active", timeout=2)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _server_up(), reason="VEGA 서버(localhost:8100) 미가동 — 스트리밍/세션 E2E 건너뜀"
)


def _mksession(title: str) -> str:
    req = urllib.request.Request(
        BASE + "/api/sessions",
        data=json.dumps({"title": title}).encode(),
        headers={"content-type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))["uuid"]


def _delete_session(sid: str) -> None:
    try:
        req = urllib.request.Request(BASE + f"/api/sessions/{sid}", method="DELETE")
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


def test_interrupt_shows_notice():
    """INT-1566: SSE가 done 없이 끊기고 재연결 한도(MAX_RECONNECTS)에 도달하면
    중단 안내('...끊겼어요')가 표시된다 — 무한 재연결로 인한 '무알림 멈춤' 방지.
    page.route로 매번 token만 보내고 done 없이 종료시켜 LLM 없이 재현한다.
    회귀: MAX_RECONNECTS 가드가 빠지거나 showError가 토큰 유무로 조건화되면 빨간불."""
    def handle_route(route):
        route.fulfill(
            status=200,
            headers={"content-type": "text/event-stream", "cache-control": "no-cache"},
            body='id: 0\nevent: token\ndata: {"token":"부분 응답"}\n\n',
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pg = browser.new_page()
        pg.goto(BASE + "/chat", wait_until="domcontentloaded")
        pg.wait_for_timeout(1500)
        pg.route("**/api/chat/stream", handle_route)
        pg.fill("#input", "중단 알림 테스트")
        pg.click("#send")
        # 재연결 한도(1+2+4+8s 백오프 × 상한) 소진 후 .error-msg 출현
        pg.wait_for_selector(".msg-row.assistant .error-msg", timeout=40000)
        txt = pg.eval_on_selector(
            ".msg-row.assistant:last-of-type .error-msg", "el => el.innerText"
        )
        browser.close()

    assert "끊" in txt, f"중단 안내 문구가 아님: {txt!r}"


def test_session_switch_optimistic():
    """INT-1565: 세션 2개를 번갈아 클릭하면 헤더(currentSid)가 클릭한 세션으로 즉시 전환된다.
    loadSession 경합/초기 자동로드로 엉뚱한 세션이 활성화되던 회귀를 막는다(낙관적 전환).
    LLM 비의존 — 빈 세션 클릭만. 회귀: myseq 가드/낙관적 currentSid 설정이 빠지면 빨간불."""
    a = _mksession("E2E-SwitchA")
    b_sid = _mksession("E2E-SwitchB")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            pg = browser.new_page()
            pg.goto(BASE + "/chat", wait_until="domcontentloaded")
            pg.wait_for_timeout(3500)  # 초기 자동 세션 로드 완료 대기
            pg.wait_for_selector(f'.session-item[data-sid="{a}"]', timeout=8000)

            def click_reach(sid: str):
                pg.click(f'.session-item[data-sid="{sid}"]')
                pg.wait_for_function(
                    "(document.getElementById('status-session-id')||{}).textContent"
                    f".startsWith('{sid[:8]}')",
                    timeout=8000,
                )

            click_reach(a)
            click_reach(b_sid)
            click_reach(a)  # B→A 연속 전환(경합 케이스)
            active = pg.evaluate(
                "(document.querySelector('.session-item.active')||{}).dataset?.sid || null"
            )
            browser.close()
        assert active == a, f"활성 세션 하이라이트 불일치: {active} != {a}"
    finally:
        _delete_session(a)
        _delete_session(b_sid)
