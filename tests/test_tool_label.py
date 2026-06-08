# Created: 2026-06-06
# Purpose: 도구 라벨에서 셸 명령의 핵심 프로그램 이름 추출(_core_command) 검증.
#          'cd X && find ... \( -name ...' 처럼 잘려 보이던 라벨을 'find'로 직관화.
#          전체 명령은 UI 아코디언(args)에 그대로 남으므로 정보 손실 없음.
# Dependencies: web.server
# Test Status: 검증 중

from __future__ import annotations

import importlib

import pytest

server = importlib.import_module("web.server")


@pytest.mark.parametrize("command,expected", [
    ("cd /Users/unohee/dev/alv-dump && find . -maxdepth 4 -name '*.js'", "find"),
    ("cd X && python3 - <<'PY'\nprint(1)\nPY", "python3"),
    ("grep -R 'cwr' .", "grep"),
    ("FOO=1 BAR=2 python3 script.py", "python3"),         # env 할당 프리픽스
    ("cat x.txt | grep foo", "cat"),                       # 파이프 → 첫 실질 명령
    ("/usr/bin/find . -name x", "find"),                   # 절대경로 → basename
    ("cd /tmp && sed -n '1,50p' file.py", "sed"),
    ("ls -la", "ls"),
    ("export PATH=/x && npm run build", "npm"),
    ("sudo systemctl restart nginx", "systemctl"),         # 래퍼 sudo 건너뜀
    ("env -i python3 x.py", "python3"),                    # 래퍼 + 옵션
    ("nice -n 5 make", "make"),                            # 래퍼 + 값 인자
    ("time pytest", "pytest"),
    ("cd a && cd b && go build", "go"),                    # 다중 준비 세그먼트
    ("", ""),
])
def test_core_command(command, expected):
    assert server._core_command(command) == expected


def test_only_prep_returns_first():
    """전부 준비성 토큰이면(cd만) 첫 토큰이라도 반환 — 빈 라벨 방지."""
    assert server._core_command("cd /only/dir") == "cd"


def test_host_exec_label_uses_core():
    """host_exec 라벨이 전체 명령이 아니라 핵심 명령 이름을 쓴다."""
    label = server._tool_label("host_exec", {"command": "cd /x && find . -name '*.py' -maxdepth 3"})
    assert "find" in label
    assert "maxdepth" not in label       # 긴 명령이 잘려 들어가지 않음
    assert "cd /x" not in label


def test_bash_exec_label_uses_core():
    label = server._tool_label("bash_exec", {"command": "FOO=1 python3 build.py"})
    assert "python3" in label


def test_host_exec_label_empty_command():
    """명령이 없으면 일반 폴백 라벨."""
    label = server._tool_label("host_exec", {})
    assert "실행" in label  # 깨지지 않고 폴백
