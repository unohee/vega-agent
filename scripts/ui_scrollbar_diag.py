# Created: 2026-05-26
# Purpose: 우측 스크롤바가 왜 뜨는지 진단 — body/layout/각 패널의 scrollHeight vs clientHeight 비교
from __future__ import annotations
from playwright.sync_api import sync_playwright


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto("http://localhost:8100/chat", wait_until="networkidle")  # cxt-ignore: fake_data
        page.wait_for_timeout(800)

        info = page.evaluate(
            """
            (() => {
              const ids = ['layout','sidebar','chat-area','messages','terminal-panel','dir-panel','input-area','statusbar'];
              const result = {
                viewport: {w: window.innerWidth, h: window.innerHeight},
                html: { scrollH: document.documentElement.scrollHeight, clientH: document.documentElement.clientHeight, scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth },
                body: { scrollH: document.body.scrollHeight, clientH: document.body.clientHeight, scrollW: document.body.scrollWidth, clientW: document.body.clientWidth },
                bodyOverflow: getComputedStyle(document.body).overflow,
                htmlOverflow: getComputedStyle(document.documentElement).overflow,
              };
              for (const id of ids) {
                const el = document.getElementById(id);
                if (!el) { result[id] = null; continue; }
                const cs = getComputedStyle(el);
                const r = el.getBoundingClientRect();
                result[id] = {
                  rect_h: Math.round(r.height),
                  scrollH: el.scrollHeight,
                  clientH: el.clientHeight,
                  overflowY: cs.overflowY,
                  hasVScroll: el.scrollHeight > el.clientHeight,
                };
              }
              // 어떤 요소가 viewport보다 크게 만드는지
              const tooTall = [];
              for (const el of document.body.querySelectorAll('*')) {
                const r = el.getBoundingClientRect();
                if (r.bottom > window.innerHeight + 1) {
                  tooTall.push({tag: el.tagName, id: el.id, classes: el.className, bottom: Math.round(r.bottom)});
                  if (tooTall.length >= 6) break;
                }
              }
              result.tooTall = tooTall;
              return result;
            })()
            """
        )
        print(f"viewport: {info['viewport']}")
        print(f"html: scrollH={info['html']['scrollH']} clientH={info['html']['clientH']} (diff={info['html']['scrollH']-info['html']['clientH']})  overflow={info['htmlOverflow']}")
        print(f"body: scrollH={info['body']['scrollH']} clientH={info['body']['clientH']} (diff={info['body']['scrollH']-info['body']['clientH']})  overflow={info['bodyOverflow']}")
        print()
        for k, v in info.items():
            if isinstance(v, dict) and 'rect_h' in v:
                marker = '⚠' if v.get('hasVScroll') else ' '
                print(f"  {marker} {k:18s}  rect={v['rect_h']:4d}  scroll={v['scrollH']:4d}  client={v['clientH']:4d}  overflow-y={v['overflowY']}")
        print()
        print("viewport 아래로 빠져나간 요소 (bottom > 900):")
        for el in info['tooTall']:
            print(f"  {el['tag']:8s} #{el['id']:18s}  bottom={el['bottom']}  classes={el['classes']!r:40s}")

        page.screenshot(path="/tmp/vega_scrollbar.png", full_page=False)
        browser.close()


if __name__ == "__main__":
    run()
