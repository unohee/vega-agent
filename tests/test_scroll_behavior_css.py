# Created: 2026-06-08
# Purpose: #messages가 scroll-behavior:smooth가 아님을 검증 (INT-1411 계열).
#          smooth면 자동 스크롤(scrollBottom)이 애니메이션으로 진행돼 사용자 스크롤을
#          압도하고, 중간 scroll 이벤트가 stick 판정을 교란해 위로 올려도 계속 따라간다.
# Dependencies: Google Chrome (headless), web/static/chat.html

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path

import pytest

_CHAT = Path(__file__).parent.parent / "web" / "static" / "chat.html"

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


@pytest.mark.skipif(_find_chrome() is None, reason="Chrome 미설치")
def test_messages_scroll_behavior_not_smooth():
    """#messages의 computed scroll-behavior가 smooth가 아니어야 한다(auto).
    smooth면 스트리밍 자동 스크롤이 사용자 위로-스크롤을 압도한다."""
    assert _CHAT.exists()
    # chat.html 로드 후 #messages의 computed scroll-behavior를 title로 노출
    probe = f"""<!doctype html><html><body><iframe id=f></iframe><script>
    fetch('file://{_CHAT.resolve()}').then(r=>r.text()).then(html=>{{
      const doc = document.getElementById('f').contentDocument;
      doc.open(); doc.write(html); doc.close();
      setTimeout(()=>{{
        const m = doc.getElementById('messages');
        const sb = m ? getComputedStyle(m).scrollBehavior : 'NO_MESSAGES';
        const sink=document.createElement('pre'); sink.id='result-sink';
        sink.textContent='RESULT_JSON:'+JSON.stringify({{scrollBehavior:sb}});
        document.body.appendChild(sink);
      }}, 300);
    }});
    </script></body></html>"""
    with tempfile.TemporaryDirectory() as tmp:
        probe_path = Path(tmp) / "probe.html"
        probe_path.write_text(probe, encoding="utf-8")
        cmd = [
            _find_chrome(), "--headless", "--disable-gpu", "--no-sandbox",
            "--allow-file-access-from-files", "--virtual-time-budget=4000",
            f"--user-data-dir={tmp}/ud", "--dump-dom", f"file://{probe_path}",
        ]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, start_new_session=True)
        try:
            dom, _ = p.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()
            dom, _ = p.communicate()

    m = re.search(r"RESULT_JSON:(\{.*?\})</pre>", dom, re.DOTALL)
    # iframe cross-doc 접근이 헤드리스에서 막히면 소스 정적 검사로 폴백
    if not m:
        src = _CHAT.read_text(encoding="utf-8")
        block = re.search(r"#messages\s*\{[^}]*\}", src)
        assert block, "#messages 블록 못 찾음"
        assert "scroll-behavior: smooth" not in block.group(0), \
            "#messages에 scroll-behavior:smooth가 남아있음 — 자동 스크롤이 사용자 스크롤 압도"
        return
    import json
    sb = json.loads(m.group(1)).get("scrollBehavior", "")
    assert sb != "smooth", f"#messages scroll-behavior가 smooth — 자동 스크롤 압도 위험 (got {sb})"
