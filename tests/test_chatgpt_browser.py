# Created: 2026-06-13
# Purpose: ChatGPT PKCE 브라우저 열기 크로스플랫폼 회귀 (INT-1505)
#   Windows 에서 subprocess.Popen(["start", url]) 은 "start" 가 cmd.exe 내장
#   명령이라 FileNotFoundError 로 브라우저가 안 떴다 → os.startfile 사용.
# Dependencies: pipeline/auth/chatgpt.py

from __future__ import annotations

from unittest import mock

import pipeline.auth.chatgpt as cg


def test_open_browser_windows_uses_startfile():
    with mock.patch.object(cg.sys, "platform", "win32"):
        fake_os = mock.MagicMock()
        with mock.patch.object(cg, "os", fake_os):
            cg._open_browser("https://auth.openai.com/x")
    fake_os.startfile.assert_called_once_with("https://auth.openai.com/x")


def test_open_browser_macos_uses_open():
    with mock.patch.object(cg.sys, "platform", "darwin"):
        with mock.patch.object(cg.subprocess, "Popen") as popen:
            cg._open_browser("https://x")
    assert popen.call_args[0][0][0] == "open"


def test_open_browser_linux_uses_xdg_open():
    with mock.patch.object(cg.sys, "platform", "linux"):
        with mock.patch.object(cg.subprocess, "Popen") as popen:
            cg._open_browser("https://x")
    assert popen.call_args[0][0][0] == "xdg-open"


def test_open_browser_never_uses_start_command():
    """회귀 가드: 어떤 플랫폼에서도 'start' 를 Popen 인자로 넘기지 않는다."""
    for plat in ("win32", "darwin", "linux"):
        with mock.patch.object(cg.sys, "platform", plat):
            with mock.patch.object(cg, "os", mock.MagicMock()):
                with mock.patch.object(cg.subprocess, "Popen") as popen:
                    cg._open_browser("https://x")
                    for call in popen.call_args_list:
                        argv = call[0][0]
                        assert argv[0] != "start", f"{plat}: 'start' 명령 사용 — Windows 에서 깨짐"
