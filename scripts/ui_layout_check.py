# Created: 2026-05-26
# Purpose: E2E로 chat.html 레이아웃 검증 — Playwright로 실제 그리드 자식 위치 추출
# Usage:   python scripts/ui_layout_check.py [--screenshot out.png]

from __future__ import annotations
import sys
from playwright.sync_api import sync_playwright


def check(url: str = "http://localhost:8100/chat", screenshot: str | None = None) -> int:  # cxt-ignore: fake_data
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(800)  # JS init 대기

        layout = page.evaluate(
            """
            (() => {
              const L = document.getElementById('layout');
              const sb = document.getElementById('sidebar');
              const ca = document.getElementById('chat-area');
              const dp = document.getElementById('dir-panel');
              const overlay = document.getElementById('sidebar-overlay');
              const term = document.getElementById('terminal-panel');
              const sbar = document.getElementById('statusbar');
              const hdr  = document.querySelector('header');

              const rect = el => el ? (() => { const r = el.getBoundingClientRect(); return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; })() : null;
              const gridChildren = L ? [...L.children].map(c => ({id: c.id || c.tagName, classes: c.className, rect: rect(c)})) : [];

              return {
                viewport: {w: window.innerWidth, h: window.innerHeight},
                layout: rect(L),
                layout_grid_cols: L ? getComputedStyle(L).gridTemplateColumns : null,
                layout_display: L ? getComputedStyle(L).display : null,
                layout_classes: L ? L.className : null,
                gridChildren,
                sidebar: rect(sb),
                chat_area: rect(ca),
                dir_panel: rect(dp),
                dir_panel_display: dp ? getComputedStyle(dp).display : null,
                dir_panel_classes: dp ? dp.className : null,
                overlay_visible: overlay ? getComputedStyle(overlay).display : null,
                terminal: rect(term),
                statusbar: rect(sbar),
                header: rect(hdr),
              };
            })()
            """
        )

        print("=" * 68)
        print(f"viewport: {layout['viewport']}")
        print(f"header:    {layout['header']}")
        print(f"layout:    {layout['layout']}  display={layout['layout_display']}  classes={layout['layout_classes']}")
        print(f"  grid-template-columns: {layout['layout_grid_cols']}")
        print(f"statusbar: {layout['statusbar']}")
        print()
        print(f"#layout children (grid order):")
        for i, c in enumerate(layout["gridChildren"]):
            print(f"  [{i}] {c['id']:20s} classes={c['classes']!r:30s} rect={c['rect']}")
        print()
        print(f"sidebar:    {layout['sidebar']}")
        print(f"chat_area:  {layout['chat_area']}")
        print(f"dir_panel:  {layout['dir_panel']}  display={layout['dir_panel_display']}  classes={layout['dir_panel_classes']!r}")
        print(f"terminal:   {layout['terminal']}")
        print(f"overlay:    display={layout['overlay_visible']}")
        print()

        # 진단
        problems = []
        sb = layout["sidebar"]
        ca = layout["chat_area"]
        dp = layout["dir_panel"]
        if sb and ca:
            if sb["x"] > ca["x"]:
                problems.append(f"sidebar(x={sb['x']})가 chat-area(x={ca['x']})보다 오른쪽에 있음 — 좌측이 아님")
            if sb["w"] > 320:
                problems.append(f"sidebar width {sb['w']}px > 320 — 너무 넓음")
        if dp and dp["w"] > 0:
            if dp["y"] > (ca["y"] + ca["h"] if ca else 0):
                problems.append(f"dir-panel이 chat-area 아래로 떨어짐 (y={dp['y']})")
        if layout["layout_display"] != "grid":
            problems.append(f"#layout display가 grid가 아님: {layout['layout_display']}")

        if problems:
            print("❌ 진단된 문제:")
            for prob in problems:
                print(f"  - {prob}")
            ret = 1
        else:
            print("✓ 레이아웃 OK")
            ret = 0

        if screenshot:
            page.screenshot(path=screenshot, full_page=False)
            print(f"\n스크린샷 저장: {screenshot}")

        browser.close()
        return ret


if __name__ == "__main__":
    args = sys.argv[1:]
    shot = None
    if "--screenshot" in args:
        i = args.index("--screenshot")
        shot = args[i + 1] if i + 1 < len(args) else "/tmp/vega_layout.png"
    sys.exit(check(screenshot=shot))
