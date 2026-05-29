# Created: 2026-05-26
# Purpose: dir/terminal 패널 열린 상태도 검증
from __future__ import annotations
import sys
from playwright.sync_api import sync_playwright

URL = "http://localhost:8100/chat"


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(800)

        # dir-panel 열기
        page.click("#toggle-dir")
        page.wait_for_timeout(300)
        page.screenshot(path="/tmp/vega_dir_open.png")

        layout = page.evaluate(
            """
            (() => {
              const rect = el => el ? (() => { const r = el.getBoundingClientRect(); return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; })() : null;
              return {
                sidebar:   rect(document.getElementById('sidebar')),
                chat_area: rect(document.getElementById('chat-area')),
                dir_panel: rect(document.getElementById('dir-panel')),
                grid_cols: getComputedStyle(document.getElementById('layout')).gridTemplateColumns,
              };
            })()
            """
        )
        print("[dir-panel 열림]")
        print(f"  grid-cols: {layout['grid_cols']}")
        print(f"  sidebar:    {layout['sidebar']}")
        print(f"  chat_area:  {layout['chat_area']}")
        print(f"  dir_panel:  {layout['dir_panel']}")

        # 터미널 토글 (세션이 있어야 함 — 첫 세션 자동 로드 기다리고)
        page.wait_for_timeout(500)
        page.click("#toggle-terminal")
        page.wait_for_timeout(800)
        page.screenshot(path="/tmp/vega_term_open.png")

        term = page.evaluate(
            """
            (() => {
              const rect = el => el ? (() => { const r = el.getBoundingClientRect(); return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; })() : null;
              const tp = document.getElementById('terminal-panel');
              return {
                terminal:    rect(tp),
                term_hidden: tp.classList.contains('hidden'),
                chat_area:   rect(document.getElementById('chat-area')),
              };
            })()
            """
        )
        print("\n[터미널 열림]")
        print(f"  terminal:  {term['terminal']}  hidden={term['term_hidden']}")
        print(f"  chat_area: {term['chat_area']}")

        # 사이드바 닫기
        page.click("#toggle-sidebar")
        page.wait_for_timeout(300)
        page.screenshot(path="/tmp/vega_sidebar_closed.png")

        sb = page.evaluate(
            """
            (() => {
              const rect = el => el ? (() => { const r = el.getBoundingClientRect(); return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; })() : null;
              return {
                sidebar:   rect(document.getElementById('sidebar')),
                chat_area: rect(document.getElementById('chat-area')),
                grid_cols: getComputedStyle(document.getElementById('layout')).gridTemplateColumns,
              };
            })()
            """
        )
        print("\n[사이드바 닫힘]")
        print(f"  grid-cols: {sb['grid_cols']}")
        print(f"  sidebar:    {sb['sidebar']}")
        print(f"  chat_area:  {sb['chat_area']}")

        browser.close()


if __name__ == "__main__":
    run()
