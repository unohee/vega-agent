# Created: 2026-05-21
# Purpose: pipeline/tools_code.py 단위 테스트 — 세이프가드 + rm 재작성
# Dependencies: pipeline/tools_code.py
# Test Status: 신규

from __future__ import annotations

import pytest


# tools_code 임포트 시 CHART_DIR.mkdir 호출됨 — 허용
import subprocess
from unittest.mock import MagicMock, patch

from pipeline.tools_code import (
    _HOST_ALLOWLIST,
    _IS_WINDOWS,
    _check_python_safeguards,
    _check_safeguards,
    _rewrite_rm,
    bash_exec,
    host_exec,
    python_exec,
)


class TestBashExec:
    def test_safeguard_blocks(self):
        result = bash_exec(":(){:|:&};:")
        assert "error" in result
        assert result["returncode"] == -1

    def test_simple_echo(self):
        result = bash_exec("echo hello")
        assert result["returncode"] == 0
        assert "hello" in result["stdout"]

    def test_rm_rewrite_adds_warning(self):
        # subprocess mock으로 실제 실행 없이 경로만 테스트
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            result = bash_exec("rm /tmp/dummy_file_test")
        assert "warnings" in result

    def test_timeout_returns_error(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
            result = bash_exec("sleep 999", timeout=1)
        assert "error" in result
        assert "타임아웃" in result["error"]

    def test_exception_returns_error(self):
        with patch("subprocess.run", side_effect=OSError("실행 실패")):
            result = bash_exec("some_command")
        assert "error" in result

    def test_utf8_encoding_explicit(self):
        """Windows 기본 locale(CP949) 디코딩으로 모지바케/예외가 나지 않도록
        subprocess.run 에 encoding='utf-8', errors='replace' 가 명시돼야 한다 (INT-1505)."""
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as run:
            bash_exec("echo 한글")
        kwargs = run.call_args.kwargs
        assert kwargs.get("encoding") == "utf-8", "bash_exec subprocess.run 에 encoding='utf-8' 누락"
        assert kwargs.get("errors") == "replace", "bash_exec subprocess.run 에 errors='replace' 누락"


class TestHostExecEncoding:
    def test_host_exec_popen_utf8(self):
        """host_exec 의 Popen 도 UTF-8 고정 디코딩 (CP949 회귀 방지, INT-1505)."""
        # allowlist 통과시키려 ask='off' 로 강제 실행 경로 진입
        with patch("subprocess.Popen") as popen:
            proc = popen.return_value
            proc.stdout = []
            proc.stderr = []
            proc.wait.return_value = 0
            proc.returncode = 0
            host_exec("echo 한글", ask="off")
        kwargs = popen.call_args.kwargs
        assert kwargs.get("encoding") == "utf-8", "host_exec Popen 에 encoding='utf-8' 누락"
        assert kwargs.get("errors") == "replace", "host_exec Popen 에 errors='replace' 누락"


class TestRewriteRm:
    def test_simple_rm(self):
        out, warns = _rewrite_rm("rm /tmp/foo.txt")
        assert "trash /tmp/foo.txt" in out
        assert warns

    def test_rm_rf(self):
        out, warns = _rewrite_rm("rm -rf /tmp/old")
        assert "trash /tmp/old" in out
        assert any("rm" in w for w in warns)

    def test_no_rm(self):
        out, warns = _rewrite_rm("ls -la /tmp")
        assert out == "ls -la /tmp"
        assert warns == []

    def test_multiple_rm(self):
        out, warns = _rewrite_rm("rm /a && rm /b")
        assert "trash /a" in out
        assert "trash /b" in out
        assert len(warns) == 2

    def test_rm_flags_stripped(self):
        out, _ = _rewrite_rm("rm -f /tmp/x")
        # trash 명령으로 변환, rm 은 사라짐
        assert "rm" not in out
        assert "trash" in out


class TestCheckSafeguards:
    def test_fork_bomb_blocked(self):
        result = _check_safeguards(":(){:|:&};:")
        assert result is not None
        assert "SAFEGUARD" in result

    def test_mkfs_blocked(self):
        result = _check_safeguards("mkfs.ext4 /dev/sdb1")
        assert result is not None

    def test_dd_blocked(self):
        result = _check_safeguards("dd if=/dev/zero of=/dev/sda")
        assert result is not None

    def test_rm_rf_root_blocked(self):
        result = _check_safeguards("rm -rf /")
        assert result is not None

    def test_rm_rf_home_blocked(self):
        result = _check_safeguards("rm -rf ~")
        assert result is not None

    def test_env_cat_blocked(self):
        result = _check_safeguards("cat .env")
        assert result is not None

    def test_safe_command_passes(self):
        assert _check_safeguards("ls -la /tmp") is None
        assert _check_safeguards("echo hello") is None
        assert _check_safeguards("pwd") is None

    def test_grep_safe(self):
        assert _check_safeguards("grep -r 'pattern' ./src") is None


@pytest.mark.skipif(_IS_WINDOWS, reason="POSIX(bash) 명령 전제 — Windows 동등 검증은 TestHostExecWindows")
class TestHostExec:
    def test_hard_blocked_rm_rf_root(self):
        result = host_exec("rm -rf /", ask="off")
        assert "error" in result
        assert "SAFEGUARD" in result["error"]

    def test_hard_blocked_fork_bomb(self):
        result = host_exec(":(){:|:&};:", ask="off")
        assert "error" in result

    def test_allowlist_mv_runs(self):
        # mv 명령은 allowlist — ask="on-miss"여도 approval 없이 실행 시도
        # 실제 mv 실행 없이 approval 반환 안 되는지만 검사
        result = host_exec("mv /nonexistent_src /nonexistent_dst", ask="on-miss")
        # approval이 아니라 실행 결과(returncode 있거나 error)여야 함
        assert "__needs_approval__" not in result

    def test_non_allowlist_returns_approval(self):
        result = host_exec("ls -la /tmp", ask="on-miss")
        assert result.get("__needs_approval__") is True

    def test_ask_off_bypasses_approval(self):
        result = host_exec("echo hello", ask="off")
        # 실행 결과 반환 (approval 아님)
        assert "__needs_approval__" not in result
        assert "stdout" in result

    def test_ask_always_returns_approval_even_on_allowlist(self):
        result = host_exec("mv /a /b", ask="always")
        assert result.get("__needs_approval__") is True

    def test_secret_blocked(self):
        result = host_exec("cat .env", ask="off")
        assert "error" in result


class TestHostExecWindows:
    """host_exec Windows PowerShell 분기 (INT-1506).

    모듈 reload 는 전역 상태(contextvar·다른 모듈의 import 참조)를 오염시켜
    pytest-randomly 순서에서 무관한 테스트를 깨뜨린다. 그래서 reload 대신
    런타임에 읽히는 모듈 상수(_IS_WINDOWS·_HOST_ALLOWLIST)만 patch.object 로
    일시 치환한다 — host_exec 와 셸 분기는 모두 모듈 전역을 그때그때 참조한다.
    """

    def test_windows_uses_powershell_argv(self):
        import pipeline.tools_code as tc
        captured = {}

        class _FakeProc:
            returncode = 0
            def __init__(self):
                self.stdout = iter(["ok\n"]); self.stderr = iter([])
            def wait(self, timeout=None): return 0
            def kill(self): pass

        def _fake_popen(args, **kw):
            captured["args"] = args; captured["shell"] = kw.get("shell")
            return _FakeProc()

        with patch.object(tc, "_IS_WINDOWS", True), \
             patch.object(tc, "_HOST_ALLOWLIST", tc._HOST_ALLOWLIST_WINDOWS), \
             patch.object(tc.subprocess, "Popen", _fake_popen):
            tc.host_exec("Move-Item a.txt b.txt", ask="off")

        argv = captured["args"]
        assert captured["shell"] is False  # cmd.exe(shell=True) 아님
        assert argv[0] == "powershell"
        assert "-NoProfile" in argv and "-Command" in argv
        # UTF-8 출력 프리픽스 + 사용자 명령이 마지막 인자에 결합
        assert "UTF8Encoding" in argv[-1]
        assert "Move-Item a.txt b.txt" in argv[-1]

    def test_windows_allowlist_is_powershell(self):
        import pipeline.tools_code as tc
        assert "move-item " in tc._HOST_ALLOWLIST_WINDOWS
        assert "copy-item " in tc._HOST_ALLOWLIST_WINDOWS
        # POSIX 전용 명령은 Windows allowlist 에 없다
        assert "mv " not in tc._HOST_ALLOWLIST_WINDOWS

    def test_windows_hard_block_format_volume(self):
        import pipeline.tools_code as tc
        with patch.object(tc, "_IS_WINDOWS", True), \
             patch.object(tc, "_HOST_ALLOWLIST", tc._HOST_ALLOWLIST_WINDOWS):
            # cmdlet 대소문자 무관 — 소문자/혼용 모두 차단
            r = tc.host_exec("Format-Volume -DriveLetter C", ask="off")
        assert "error" in r and "SAFEGUARD" in r["error"]

    def test_windows_no_rm_to_trash_rewrite(self):
        import pipeline.tools_code as tc
        with patch.object(tc, "_IS_WINDOWS", True), \
             patch.object(tc, "_HOST_ALLOWLIST", tc._HOST_ALLOWLIST_WINDOWS):
            # Windows 엔 trash 명령이 없으므로 rm→trash 치환 비활성.
            # Remove-Item 은 allowlist 미포함 → 승인요청, command 원형 유지(trash 흔적 없음)
            r = tc.host_exec("Remove-Item x.txt", ask="on-miss")
        assert r.get("__needs_approval__") is True
        assert r["command"] == "Remove-Item x.txt"
        assert "trash" not in r["command"]

    def test_posix_rm_to_trash_still_active(self):
        # 회귀 가드: POSIX(현재 플랫폼)에선 rm→trash 가 여전히 동작
        import pipeline.tools_code as tc
        rewritten, warns = tc._rewrite_rm("rm -rf /tmp/x")
        assert rewritten.startswith("trash") and warns


class TestPythonExec:
    """python_exec — 세이프가드 + 실행 경로"""

    def test_safeguard_blocks_env_open(self):
        result = python_exec("with open('.env') as f: pass")
        assert result["returncode"] == -1
        assert "SAFEGUARD" in result.get("error", "")

    def test_simple_print(self):
        mock_result = MagicMock()
        mock_result.stdout = "42\n"
        mock_result.stderr = ""
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            result = python_exec("print(6 * 7)")
        assert result["returncode"] == 0
        assert "42" in result["stdout"]

    def test_timeout_returns_error(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("python", 60)):
            result = python_exec("import time; time.sleep(9999)")
        assert result["returncode"] == -1
        assert "타임아웃" in result.get("error", "")

    def test_exception_returns_error(self):
        with patch("subprocess.run", side_effect=OSError("실행 불가")):
            result = python_exec("print('hi')")
        assert "error" in result
        assert result["returncode"] == -1


class TestCheckPythonSafeguards:
    def test_open_env_blocked(self):
        code = "with open('.env') as f: data = f.read()"
        result = _check_python_safeguards(code)
        assert result is not None
        assert "SAFEGUARD" in result

    def test_open_token_blocked(self):
        # open('chatgpt_token.json') 형태는 차단
        code = "f = open('chatgpt_token.json')"
        result = _check_python_safeguards(code)
        assert result is not None

    def test_safe_code_passes(self):
        code = "import json\ndata = json.loads(text)"
        assert _check_python_safeguards(code) is None

    def test_normal_open_passes(self):
        code = "open('output.txt', 'w').write('hello')"
        assert _check_python_safeguards(code) is None
