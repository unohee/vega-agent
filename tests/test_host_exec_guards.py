# Created: 2026-06-23
# Purpose: 호스트 코드 실행의 결정적 안전 가드 회귀 잠금 (INT-1870 Phase B).
#          Docker(심층방어) 제거 후 이 가드가 bash/python/host_exec 의 유일한 안전층이므로
#          파괴/시크릿 차단이 약화되면 즉시 빨간불을 내야 한다.
# Dependencies: pipeline.tools_code
# Test Status: green (2026-06-23)

from __future__ import annotations

from pipeline.tools_code import _check_python_safeguards, _check_safeguards


class TestBashGuards:
    """bash_exec/host_exec 가 거치는 정적 명령 가드(_check_safeguards)."""

    def test_fork_bomb_blocked(self):
        assert _check_safeguards(":(){:|:&};:") is not None

    def test_mkfs_blocked(self):
        assert _check_safeguards("mkfs.ext4 /dev/sda1") is not None

    def test_rm_rf_root_blocked(self):
        assert _check_safeguards("rm -rf /") is not None

    def test_secret_read_ssh_key_blocked(self):
        assert _check_safeguards("cat ~/.ssh/id_rsa") is not None

    def test_secret_read_dotenv_blocked(self):
        assert _check_safeguards("cat .env") is not None

    def test_safe_commands_allowed(self):
        assert _check_safeguards("ls -la") is None
        assert _check_safeguards("echo hello") is None
        assert _check_safeguards("python script.py") is None


class TestPythonGuards:
    """python_exec 가 거치는 정적 코드 가드(_check_python_safeguards)."""

    def test_secret_read_open_dotenv_blocked(self):
        assert _check_python_safeguards("open('.env').read()") is not None

    def test_secret_read_oauth_token_blocked(self):
        assert _check_python_safeguards("data = open('openai_oauth.json').read_text()") is not None

    def test_safe_code_allowed(self):
        assert _check_python_safeguards("print(2 + 2)") is None

    def test_normal_file_ops_allowed(self):
        # openpyxl.save / 일반 파일 작업은 막지 않는다 (office 도구가 호스트로 동작해야 함)
        assert _check_python_safeguards("import openpyxl; openpyxl.Workbook().save('/tmp/a.xlsx')") is None
