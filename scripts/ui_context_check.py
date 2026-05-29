# Created: 2026-05-26
# Purpose: 컨텍스트 패널 + + 버튼 메뉴 E2E 검증
from __future__ import annotations
import sys
from playwright.sync_api import sync_playwright

URL = "http://localhost:8100/chat"


def run(screenshot: str | None = None) -> int:
    failures = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(viewport={"width": 1440, "height": 900}).new_page()

        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(1500)

        # 1. statusbar 토큰 카운트 표시
        page.wait_for_timeout(1000)  # context-changed 이벤트 후 fetch 시간
        token_label = page.evaluate("document.getElementById('status-tokens-label').textContent")
        if "토큰" not in token_label or token_label.startswith("—"):
            # 첫 fetch가 안 됐을 수 있으니 다시 트리거
            page.evaluate("window.dispatchEvent(new Event('vega:context-changed'))")
            page.wait_for_timeout(1500)
            token_label = page.evaluate("document.getElementById('status-tokens-label').textContent")
        if "토큰" not in token_label or token_label == "— 토큰":
            failures.append(f"statusbar 토큰 라벨 미갱신: {token_label!r}")

        # 2. dir-panel 열고 '컨텍스트' 탭 클릭
        page.evaluate("localStorage.setItem('vega.panels.v1', JSON.stringify({sidebar:true, dir:true}))")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(2500)

        # 탭이 보이는지
        tabs = page.locator(".side-tab")
        if tabs.count() != 2:
            failures.append(f".side-tab 2개 기대, 실제 {tabs.count()}")
        page.click('.side-tab[data-tab="context"]')
        page.wait_for_timeout(1000)
        sections_count = page.evaluate("document.querySelectorAll('#ctx-sections .ctx-section').length")
        if sections_count < 3:
            failures.append(f"컨텍스트 섹션 < 3개: 실제 {sections_count}")

        total_tokens = page.evaluate("document.getElementById('ctx-total-tokens').textContent")
        if total_tokens == "—":
            failures.append("ctx-total-tokens가 갱신 안 됨")

        # 3. + 버튼 클릭 → 메뉴 표시
        page.click("#plus-btn")
        page.wait_for_timeout(200)
        menu_visible = page.evaluate("() => !!document.querySelector('.plus-menu')")
        if not menu_visible:
            failures.append("+ 버튼 클릭 후 plus-menu 미표시")
        else:
            items = page.evaluate("[...document.querySelectorAll('.plus-menu .plus-item')].map(i => i.dataset.action)")
            for needed in ['attach', 'pick-workdir', 'slash', 'toggle-context', 'toggle-files', 'toggle-terminal']:
                if needed not in items:
                    failures.append(f"plus-menu에 '{needed}' 항목 누락 (실제: {items})")

        if screenshot:
            page.screenshot(path=screenshot)

        # 4. 메뉴 외부 클릭 → 닫힘
        page.mouse.click(900, 400)
        page.wait_for_timeout(200)
        still_open = page.evaluate("() => !!document.querySelector('.plus-menu')")
        if still_open:
            failures.append("plus-menu 외부 클릭으로 닫히지 않음")

        # 5. /api/context/preview 직접 호출
        preview = page.evaluate(
            """async () => {
              const r = await fetch('/api/sessions');
              const sid = (await r.json()).sessions?.[0]?.uuid;
              const r2 = await fetch('/api/context/preview?sid=' + sid);
              return {status: r2.status, data: r2.ok ? await r2.json() : null};
            }"""
        )
        if preview["status"] != 200:
            failures.append(f"/api/context/preview 응답 {preview['status']}")
        elif not preview["data"]["sections"] or preview["data"]["total_tokens_estimate"] == 0:
            failures.append(f"context preview 비어있음: {preview['data']}")

        b.close()

    if failures:
        print("❌ 실패:")
        for f in failures: print(f"  - {f}")
        return 1
    print("✓ 모든 검증 통과")
    return 0


if __name__ == "__main__":
    shot = None
    if "--screenshot" in sys.argv:
        i = sys.argv.index("--screenshot")
        shot = sys.argv[i+1] if i+1 < len(sys.argv) else "/tmp/vega_ctxpanel.png"
    sys.exit(run(screenshot=shot))
