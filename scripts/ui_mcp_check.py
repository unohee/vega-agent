# Created: 2026-05-26
# Purpose: MCP 관리 모달 E2E 검증
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

        # 1. + 버튼 → MCP 항목 클릭 → 모달 표시
        page.click("#plus-btn")
        page.wait_for_timeout(200)
        if not page.is_visible('.plus-menu .plus-item[data-action="mcp-manage"]'):
            failures.append("+ 메뉴에 'mcp-manage' 항목 없음")
            b.close(); return 1
        page.click('.plus-menu .plus-item[data-action="mcp-manage"]')
        page.wait_for_timeout(500)
        if page.evaluate("document.getElementById('mcp-modal').classList.contains('hidden')"):
            failures.append("MCP 모달이 표시 안 됨")

        # 2. 목록에 linear (auto) 표시 확인
        page.wait_for_selector("#mcp-list .mcp-row", timeout=5000)
        names = page.evaluate("[...document.querySelectorAll('#mcp-list .mcp-row-name')].map(n => n.childNodes[0]?.textContent?.trim())")
        # 첫 단어만 추출 (badge 제외)
        if not any('linear' in n for n in names):
            failures.append(f"linear 서버 미표시: {names}")

        # 3. 새 서버 추가 (폼 모드)
        page.click("#mcp-add-btn")
        page.wait_for_timeout(200)
        page.fill("#mcp-name", "test-fs-e2e")
        page.fill("#mcp-command", "npx")
        page.fill("#mcp-args", "-y\n@modelcontextprotocol/server-filesystem\n/tmp")
        page.click("#mcp-save-btn")
        page.wait_for_timeout(800)
        # 목록에 추가됐는지 확인
        names2 = page.evaluate("[...document.querySelectorAll('#mcp-list .mcp-row-name')].map(n => n.childNodes[0]?.textContent?.trim())")
        if not any('test-fs-e2e' in n for n in names2):
            failures.append(f"새 서버 'test-fs-e2e'가 목록에 안 보임: {names2}")

        if screenshot:
            page.screenshot(path=screenshot)

        # 4. 보안 검증 (rm 같은 잘못된 command)
        page.click("#mcp-add-btn")
        page.wait_for_timeout(200)
        page.fill("#mcp-name", "evil")
        page.fill("#mcp-command", "rm")
        page.fill("#mcp-args", "-rf\n/")
        page.click("#mcp-save-btn")
        page.wait_for_timeout(500)
        result_text = page.evaluate("document.getElementById('mcp-form-result').textContent")
        if "command" not in result_text:
            failures.append(f"보안 검증 실패 — 'rm' 명령이 차단되지 않음: {result_text!r}")

        # 5. JSON 모드 전환 + 잘못된 JSON
        page.click('.mcp-tab[data-mode="json"]')
        page.wait_for_timeout(150)
        page.fill("#mcp-name-json", "json-test")
        page.fill("#mcp-json", "{ this is not json }")
        page.click("#mcp-save-btn")
        page.wait_for_timeout(300)
        json_result = page.evaluate("document.getElementById('mcp-form-result').textContent")
        if "JSON 파싱 실패" not in json_result:
            failures.append(f"JSON 파싱 에러 메시지 미표시: {json_result!r}")

        # cleanup: 추가한 test-fs-e2e 삭제
        page.evaluate(
            """async () => {
              await fetch('/api/mcp/servers/test-fs-e2e', {method: 'DELETE'});
            }"""
        )

        # 6. 모달 닫기 (Esc)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        if not page.evaluate("document.getElementById('mcp-modal').classList.contains('hidden')"):
            # cancel-btn으로 폼이 먼저 닫혔을 수 있음 → 한번 더
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

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
        shot = sys.argv[i+1] if i+1 < len(sys.argv) else "/tmp/vega_mcp.png"
    sys.exit(run(screenshot=shot))
