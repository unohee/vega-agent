# Created: 2026-05-26
# Purpose: ! 셸 실행 + MCP 빠른 추가 E2E
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

        # 1. ! 셸 모드 — 입력창에 ! 타이핑 → shell-mode 클래스
        page.fill("#input", "!")
        page.wait_for_timeout(150)
        has_shell_class = page.evaluate("document.getElementById('input-area').classList.contains('shell-mode')")
        if not has_shell_class:
            failures.append("! 입력 후 input-area에 shell-mode 클래스 미부여")

        # 2. !echo 실행 → 결과 버블
        page.fill("#input", "!echo hello-shell")
        page.wait_for_timeout(150)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        # 마지막 .shell-pre 안에 'hello-shell' 포함
        shell_out = page.evaluate("[...document.querySelectorAll('.shell-pre')].pop()?.textContent")
        if not shell_out or "hello-shell" not in shell_out:
            failures.append(f"shell 실행 결과에 'hello-shell' 없음: {shell_out!r}")

        # 3. MCP 빠른 추가 — + 버튼 → MCP 관리 → 새 서버
        page.click("#plus-btn")
        page.wait_for_timeout(200)
        page.click('.plus-menu .plus-item[data-action="mcp-manage"]')
        page.wait_for_timeout(500)
        page.click("#mcp-add-btn")
        page.wait_for_timeout(200)
        # quick 탭이 active인지
        active_mode = page.evaluate("document.querySelector('.mcp-tab.active')?.dataset.mode")
        if active_mode != "quick":
            failures.append(f"+ 새 서버 시 quick 탭이 활성화 안 됨 (현재: {active_mode})")
        # 명령어 입력
        page.fill("#mcp-quick-cmd", "npx -y @modelcontextprotocol/server-filesystem /tmp")
        page.wait_for_timeout(500)  # 자동 이름 추출 debounce
        placeholder = page.evaluate("document.getElementById('mcp-quick-name').placeholder")
        if "filesystem" not in placeholder:
            failures.append(f"자동 이름 추출 안 됨: placeholder={placeholder!r}")

        if screenshot:
            page.screenshot(path=screenshot)

        # 저장
        page.click("#mcp-save-btn")
        page.wait_for_timeout(800)
        names = page.evaluate("[...document.querySelectorAll('#mcp-list .mcp-row-name')].map(n => n.childNodes[0]?.textContent?.trim())")
        if not any('filesystem' in (n or '') for n in names):
            failures.append(f"빠른 추가 후 'filesystem' 목록에 없음: {names}")

        # cleanup
        page.evaluate("fetch('/api/mcp/servers/filesystem', {method:'DELETE'})")

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
        shot = sys.argv[i+1] if i+1 < len(sys.argv) else "/tmp/vega_shell_mcp.png"
    sys.exit(run(screenshot=shot))
