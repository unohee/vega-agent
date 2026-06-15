# Created: 2026-05-18
# Purpose: VEGA code execution tools — bash, python, matplotlib/plotly charts
# Dependencies: subprocess, matplotlib, plotly, mlx_env
# Test Status: untested

from __future__ import annotations

import base64
import io
import json
import os
import re as _re
import subprocess
import sys
import tempfile
import textwrap
import traceback
from pathlib import Path

VEGA_ROOT = Path(__file__).parent.parent
MLX_PYTHON = Path.home() / "dev/mlx_env/bin/python"
CHART_DIR = VEGA_ROOT / "data" / "charts"
CHART_DIR.mkdir(parents=True, exist_ok=True)

# host_exec 셸 분기 — Windows 는 cmd.exe(shell=True 기본)가 아니라 PowerShell 로 실행한다.
# cmd.exe 는 bash/PowerShell 어느 쪽 관용구도 못 받아 LLM 재시도를 양산했다(INT-1506).
# PowerShell 은 macOS bash 의 역량(Get-ChildItem/Copy-Item/Remove-Item 등)에 가장 근접.
_IS_WINDOWS = sys.platform == "win32"

# Current session working directory — set per-request by _run_gpt_task.
# Referenced as the default cwd by bash/python/host_exec. Falls back to home if unset.
import contextvars
_WORKING_DIR: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vega_working_dir", default=None
)

# Real-time output callback for host_exec — set() by server.py on on_tool_start, reset() on on_tool_done
# Signature: callable(tag: str, line: str). tag is "stdout" or "stderr"
_HOST_EXEC_LINE_CB: contextvars.ContextVar = contextvars.ContextVar(
    "host_exec_line_cb", default=None
)


def set_session_working_dir(path: str | None) -> None:
    """Sets the working directory for the current execution context (None means home)."""
    _WORKING_DIR.set(path or None)


def _base_cwd() -> str:
    """Returns the session working directory if set and exists, otherwise home."""
    wd = _WORKING_DIR.get()
    if wd and Path(wd).expanduser().is_dir():
        return str(Path(wd).expanduser())
    return str(Path.home())

# ── Safeguards ────────────────────────────────────────────────────────────────

# 1. Unconditionally blocked patterns (fork bomb, root deletion, disk destruction)
_HARD_BLOCKED: list[tuple[str, str]] = [
    (":(){:|:&};:", "fork bomb"),
    ("mkfs",        "filesystem format"),
    ("> /dev/sda",  "disk overwrite"),
    ("dd if=",      "disk copy/destroy"),
]

# rm -rf / or rm -rf ~ (home/root without further path) blocked by separate regex
_DESTRUCTIVE_RM = _re.compile(r"\brm\s+-\S*r\S*\s+[~/]\s*$|\brm\s+-\S*r\S*\s+/\s")


# 2. Block .env / secret file disclosure via cat/echo/print combinations
_SECRET_PATTERN = _re.compile(
    r"(cat|echo|print|less|more|head|tail|bat|open)\s+.*"
    r"(\.env\b|\.env\.|openai_oauth\.json|chatgpt_token\.json|client_secret[^\s]*\.json"
    r"|keychain|credentials\.json|token\.json|refresh_token"
    r"|id_rsa|id_ed25519|id_ecdsa|\.pem\b|\.key\b|\.p12\b|\.pfx\b"
    r"|service_account.*\.json|\.netrc\b|\.npmrc\b)",
    _re.IGNORECASE,
)
# Also block bare path exposure (grep, curl, python -c "open('.env')", etc.)
_SECRET_PATH_PATTERN = _re.compile(
    r"(?<!\w)(\.env\b|\.env\.|openai_oauth\.json|chatgpt_token\.json"
    r"|client_secret[^\"'\s]*\.json"
    r"|/\.ssh/|['\"]\.ssh[/'\"]"
    r"|id_rsa|id_ed25519|service_account[^\"'\s]*\.json)(?!\w)?",
    _re.IGNORECASE,
)

# 3. Auto-rewrite rm → trash
#    Converts rm [-flags] <path...> to trash <path...>
#    Captures path portion up to whitespace + shell operators (&&, ||, ;, |, >, <)
_RM_PATTERN = _re.compile(
    r"\brm\s+((?:-[a-zA-Z]+\s+)*)([^\s;|&><][^;|&><]*?)(?=\s*(?:&&|\|\||;|&|\||>|<|$))"
)


def _rewrite_rm(command: str) -> tuple[str, list[str]]:
    """
    Rewrites rm commands to trash. Records substitutions in warnings.
    rm -rf /path → trash /path  (flags stripped, sent to trash)
    """
    warnings = []
    def _replace(m: _re.Match) -> str:
        flags = m.group(1).strip()   # e.g. "-rf"
        paths = m.group(2).strip()   # actual path(s)
        original = f"rm {flags} {paths}".strip()
        warnings.append(f"`{original}` → `trash {paths}` (moved to trash)")
        return f"trash {paths}"

    rewritten = _RM_PATTERN.sub(_replace, command)
    return rewritten, warnings


def _check_safeguards(command: str) -> str | None:
    """
    Security check for a shell command. Returns an error message on violation, None on pass.
    """
    # Hard block
    for pattern, label in _HARD_BLOCKED:
        if pattern in command:
            return f"[SAFEGUARD] blocked: {label} ({pattern!r})"

    # Block recursive home/root deletion (rm -rf ~ / rm -rf /)
    if _DESTRUCTIVE_RM.search(command):
        return "[SAFEGUARD] 차단: 재귀적 홈/루트 디렉토리 삭제 (rm -rf ~ 또는 rm -rf /)"

    # Secret file disclosure
    if _SECRET_PATTERN.search(command) or _SECRET_PATH_PATTERN.search(command):
        return (
            "[SAFEGUARD] .env 또는 시크릿 파일 직접 출력 차단.\n"
            "내용 확인이 필요하면 사용자에게 직접 요청하세요."
        )

    return None


def _check_python_safeguards(code: str) -> str | None:
    """Blocks direct secret file reads from Python code."""
    secret_reads = _re.compile(
        r"(open|read_text|read_bytes)\s*\(['\"].*"
        r"(\.env|openai_oauth\.json|chatgpt_token\.json"
        r"|client_secret[^\"']*\.json)['\"]",
        _re.IGNORECASE,
    )
    if secret_reads.search(code):
        return (
            "[SAFEGUARD] Python에서 시크릿 파일 직접 읽기 차단.\n"
            "인증 토큰이 필요하면 pipeline/tools.py의 _google_token() 등을 사용."
        )
    return None


# ── Bash execution ────────────────────────────────────────────────────────────

def bash_exec(command: str, timeout: int = 60, workdir: str | None = None) -> dict:
    """
    Executes a bash command — full home directory access.
    - rm → trash auto-rewrite (sends to trash)
    - Blocks .env / secret file disclosure
    Returns: {"stdout", "stderr", "returncode", "warnings"(if any), "error"(if any)}
    """
    # Security check
    err = _check_safeguards(command)
    if err:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": err}

    # rm → trash rewrite
    command, rm_warnings = _rewrite_rm(command)

    cwd = workdir or _base_cwd()
    env = os.environ.copy()
    mlx_bin = str(MLX_PYTHON.parent)
    env["PATH"] = f"{mlx_bin}:{env.get('PATH', '')}"
    env["VIRTUAL_ENV"] = str(MLX_PYTHON.parent.parent)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",   # CP949 디코딩 모지바케/예외 방지 (INT-1505)
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        out = {
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:1000],
            "returncode": result.returncode,
        }
        if rm_warnings:
            out["warnings"] = rm_warnings
        return out
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "", "returncode": -1,
                "error": f"타임아웃 ({timeout}초)"}
    except Exception as e:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": str(e)}


# ── Python execution ──────────────────────────────────────────────────────────

_PYTHON_PRELUDE = """
import sys, os
from pathlib import Path
# VEGA module
sys.path.insert(0, '{vega_root}')
# Major project paths
for _p in ['{home}/dev/STONKS', '{home}/dev/ArtifactNet', '{home}/dev/template-crawler']:
    if _p not in sys.path and os.path.exists(_p):
        sys.path.insert(0, _p)
os.chdir('{cwd}')
""".strip()


def python_exec(code: str, timeout: int = 60) -> dict:
    """
    Executes Python code (mlx_env) — home directory based, includes major project paths.
    Returns: {"stdout", "stderr", "returncode", "error"(if any)}
    """
    err = _check_python_safeguards(code)
    if err:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": err}

    prelude = _PYTHON_PRELUDE.format(
        vega_root=str(VEGA_ROOT), home=str(Path.home()), cwd=_base_cwd()
    )
    full_code = prelude + "\n" + textwrap.dedent(code)

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False,
                                     encoding="utf-8") as f:
        f.write(full_code)
        tmppath = f.name

    env = os.environ.copy()
    mlx_bin = str(MLX_PYTHON.parent)
    env["PATH"] = f"{mlx_bin}:{env.get('PATH', '')}"
    env["VIRTUAL_ENV"] = str(MLX_PYTHON.parent.parent)

    try:
        result = subprocess.run(
            [str(MLX_PYTHON), tmppath],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        return {
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:1000],
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "", "returncode": -1,
                "error": f"타임아웃 ({timeout}초)"}
    except Exception as e:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": str(e)}
    finally:
        Path(tmppath).unlink(missing_ok=True)


# ── Chart generation ──────────────────────────────────────────────────────────

def _save_fig_png(fig_code_result: str) -> str:
    """Returns a temporary PNG file path."""
    import uuid
    path = CHART_DIR / f"chart_{uuid.uuid4().hex[:8]}.png"
    return str(path)


def chart_matplotlib(code: str) -> dict:
    """
    Runs matplotlib code and saves output as PNG.
    Automatically saves any open figure; or call plt.savefig() directly in code.
    Returns: {"__type": "image", "path": str, "stdout": str} | {"error": str}
    """
    import uuid
    chart_path = str(CHART_DIR / f"chart_{uuid.uuid4().hex[:8]}.png")

    wrapper = f"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

{textwrap.dedent(code)}

# Auto-save any unsaved figures
_figs = [plt.figure(i) for i in plt.get_fignums()]
if _figs:
    plt.tight_layout()
    plt.savefig({repr(chart_path)}, dpi=150, bbox_inches='tight')
    plt.close('all')
print('__chart_path__:' + {repr(chart_path)})
"""
    result = python_exec(wrapper, timeout=30)
    if result.get("error"):
        return {"error": result["error"]}
    if result["returncode"] != 0:
        return {"error": result["stderr"] or "실행 실패"}

    if Path(chart_path).exists():
        return {"__type": "image", "path": chart_path, "stdout": result["stdout"]}
    return {"error": "차트 파일 생성 실패\n" + result["stderr"]}


def chart_plotly(code: str) -> dict:
    """
    Runs plotly code and saves output as HTML.
    Define fig as fig = go.Figure(...) or fig = px.xxx(...) in code.
    Returns: {"__type": "html", "path": str} | {"error": str}
    """
    import uuid
    html_path = str(CHART_DIR / f"chart_{uuid.uuid4().hex[:8]}.html")

    wrapper = f"""
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np

{textwrap.dedent(code)}

# Save if fig variable is defined
if 'fig' in dir():
    fig.write_html({repr(html_path)}, include_plotlyjs='cdn', full_html=True)
    print('__chart_path__:' + {repr(html_path)})
else:
    print('ERROR: fig 변수 없음')
"""
    result = python_exec(wrapper, timeout=30)
    if result.get("error"):
        return {"error": result["error"]}
    if result["returncode"] != 0:
        return {"error": result["stderr"] or "실행 실패"}

    if Path(html_path).exists():
        return {"__type": "html", "path": html_path}
    return {"error": "HTML 파일 생성 실패\n" + result["stderr"]}


# ── System status (host direct) ───────────────────────────────────────────────

def system_info() -> dict:
    """Returns CPU, memory, disk, and running process info (executed on host)."""
    result = bash_exec(
        "echo '=CPU=' && top -l 1 -n 0 | grep 'CPU usage' && "
        "echo '=MEM=' && top -l 1 -n 0 | grep 'PhysMem' && "
        "echo '=DISK=' && df -h / | tail -1 && "
        "echo '=DOCKER=' && docker ps --format 'table {{.Names}}\\t{{.Status}}' 2>/dev/null | head -10",
        timeout=10,
    )
    return {"info": result["stdout"], "error": result.get("error")}


# ── Host direct execution (allowlist-based) ───────────────────────────────────

# allowlist: commands starting with these patterns execute immediately without confirmation
# trash is included because rm → trash rewrites must always run directly
# 승인 없이 바로 실행되는 안전한 파일 조작 명령. 플랫폼별로 다르다.
_HOST_ALLOWLIST_POSIX: list[str] = [
    "mv ",
    "mkdir ",
    "mkdir\n",
    "cp ",
    "touch ",
    "osascript ",
    "open ",
    "rename ",
    "ditto ",
    "trash ",   # result of rm → trash rewrite — always moves to trash
]
# Windows PowerShell 등가물 (cmdlet 은 대소문자 무관이나 LLM 은 PascalCase 로 생성).
# 매칭은 소문자로 정규화해서 비교하므로 여기엔 소문자로 둔다.
_HOST_ALLOWLIST_WINDOWS: list[str] = [
    "move-item ",
    "copy-item ",
    "new-item ",
    "rename-item ",
    "set-location ",
    "get-childitem ",
    "get-content ",
    "get-item ",
    "test-path ",
    "start-process ",   # macOS open 등가 — 파일/폴더를 기본 앱으로 열기
]
_HOST_ALLOWLIST: list[str] = _HOST_ALLOWLIST_WINDOWS if _IS_WINDOWS else _HOST_ALLOWLIST_POSIX

# Patterns that are never allowed, even in host execution
_HOST_HARD_BLOCKED: list[tuple[str, str]] = [
    ("rm -rf /",   "recursive root deletion"),
    ("rm -rf ~",   "recursive home deletion"),
    (":(){:|:&};:", "fork bomb"),
    ("mkfs",       "filesystem format"),
    ("> /dev/",    "device overwrite"),
    # Windows: 휴지통 우회 영구 삭제·디스크 포맷·재귀 루트 삭제
    ("remove-item -recurse -force c:\\", "recursive root deletion"),
    ("format-volume", "filesystem format"),
    ("clear-disk", "disk wipe"),
    ("remove-item -recurse -force $home", "recursive home deletion"),
]


def host_exec(command: str, ask: str = "on-miss", timeout: int = 300) -> dict:
    """
    Executes a bash command directly on the host (outside sandbox).
    Used for operations requiring TCC permissions: iCloud Drive move/copy/mkdir, etc.

    ask modes:
      "off"      — execute immediately regardless of allowlist (use after user approval)
      "on-miss"  — return approval request when not on allowlist (default)
      "always"   — always request approval

    Returns:
      success    → {"stdout", "stderr", "returncode"}
      needs approval → {"__needs_approval__": true, "command": ..., "reason": ...}
      blocked    → {"error": ...}
    """
    # Hard block (regardless of ask mode). Windows PowerShell cmdlet 은 대소문자
    # 무관하므로 소문자 정규화해 비교한다(_HOST_HARD_BLOCKED 의 Windows 패턴도 소문자).
    _cmd_lc = command.lower()
    for pattern, label in _HOST_HARD_BLOCKED:
        if (pattern in _cmd_lc) if _IS_WINDOWS else (pattern in command):
            return {"error": f"[SAFEGUARD] 차단: {label}"}

    if _SECRET_PATTERN.search(command) or _SECRET_PATH_PATTERN.search(command):
        return {"error": "[SAFEGUARD] 시크릿 파일 접근 차단"}

    # rm → trash rewrite. POSIX 에서만 — Windows PowerShell 엔 trash 명령이 없고
    # Remove-Item 은 휴지통이 아니라 영구 삭제라 단순 치환 불가(영구삭제는 hard-block).
    rm_warnings: list[str] = []
    if not _IS_WINDOWS:
        command, rm_warnings = _rewrite_rm(command)

    # Allowlist check — Windows 는 cmdlet 대소문자 무관이므로 소문자로 비교.
    cmd_stripped = (command.lower() if _IS_WINDOWS else command).lstrip()
    on_allowlist = any(cmd_stripped.startswith(p) for p in _HOST_ALLOWLIST)

    # 셸 체인 연산자가 있으면 allowlist 통과를 취소 — `ls; curl|bash` 류 우회 방지.
    # Windows PowerShell 은 별도 파서(파이프 허용)라 제외.
    import re as _re
    _SHELL_CHAIN = _re.compile(r"[;|&`]|\$\(")
    if not _IS_WINDOWS and on_allowlist and _SHELL_CHAIN.search(command):
        on_allowlist = False

    needs_approval = (ask == "always") or (ask == "on-miss" and not on_allowlist)

    if needs_approval:
        return {
            "__needs_approval__": True,
            "command": command,
            "reason": "allowlist 미매치" if not on_allowlist else "ask=always",
        }

    # Execute — stream stdout/stderr in real time via Popen
    import threading
    env = os.environ.copy()
    # Line callback injected externally (registered by server.py on on_tool_start)
    _line_cb = _HOST_EXEC_LINE_CB.get()

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    # 셸 선택. Windows: PowerShell 로 실행(cmd.exe 가 아니라). UTF-8 출력을 위해
    # $OutputEncoding/콘솔 코드페이지를 명령 앞에 세팅하고 사용자 명령을 이어붙인다.
    # 그 외(macOS/Linux): bash shell=True.  (INT-1506)
    if _IS_WINDOWS:
        _ps_prefix = (
            "$OutputEncoding = [Console]::OutputEncoding = "
            "[Text.UTF8Encoding]::new(); "
        )
        popen_args = [
            "powershell", "-NoProfile", "-NonInteractive",
            "-Command", _ps_prefix + command,
        ]
        popen_shell = False
    else:
        popen_args = command
        popen_shell = True

    try:
        proc = subprocess.Popen(
            popen_args,
            shell=popen_shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Windows 기본 locale 인코딩(CP949 등)으로 디코딩하면 UTF-8 출력이 모지바케/
            # UnicodeDecodeError 가 되어 LLM 이 재시도를 반복한다. UTF-8 고정 + replace. (INT-1505)
            encoding="utf-8",
            errors="replace",
            cwd=_base_cwd(),
            env=env,
        )

        def _drain(stream, buf: list[str], tag: str):
            for line in stream:
                buf.append(line)
                if _line_cb:
                    try:
                        _line_cb(tag, line.rstrip("\n"))
                    except Exception:
                        pass
            stream.close()

        t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_lines, "stdout"), daemon=True)
        t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_lines, "stderr"), daemon=True)
        t_out.start(); t_err.start()

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            t_out.join(2); t_err.join(2)
            if _IS_WINDOWS:
                bg_cmd = f"Start-Job -ScriptBlock {{ {command.strip()} }}"
            else:
                bg_cmd = f"nohup {command.strip()} > /tmp/vega_bg.log 2>&1 &"
            return {
                "error": f"타임아웃 ({timeout}초)",
                "hint": "장시간 작업은 백그라운드 실행을 권장합니다.",
                "bg_command": bg_cmd,
                "stdout": "".join(stdout_lines)[:4000],
            }

        t_out.join(5); t_err.join(5)
        out: dict = {
            "stdout": "".join(stdout_lines)[:4000],
            "stderr": "".join(stderr_lines)[:1000],
            "returncode": proc.returncode,
        }
        if rm_warnings:
            out["warnings"] = rm_warnings
        return out
    except Exception as e:
        return {"error": str(e)}


# ── Sandbox wrappers (tools exposed to the LLM) ───────────────────────────────

def _sandboxed_bash(command: str, timeout: int = 60) -> dict:
    """
    Runs bash inside an isolated container.
    /host_home (ro) = host home, /vega_data (rw) = VEGA data.
    """
    from pipeline.sandbox import sandbox_bash
    return sandbox_bash(command, timeout=timeout)


def _sandboxed_python(code: str, timeout: int = 60) -> dict:
    """Runs Python inside an isolated container."""
    from pipeline.sandbox import sandbox_python
    return sandbox_python(code, timeout=timeout)


def _sandboxed_matplotlib(code: str) -> dict:
    """Runs matplotlib inside an isolated container and returns PNG."""
    from pipeline.sandbox import sandbox_matplotlib
    return sandbox_matplotlib(code)


def _sandboxed_plotly(code: str) -> dict:
    """Runs plotly inside an isolated container and returns HTML."""
    from pipeline.sandbox import sandbox_plotly
    return sandbox_plotly(code)


# ── Schemas ───────────────────────────────────────────────────────────────────

# host_exec 안내는 플랫폼에 맞춰야 한다 — Windows 에 "bash 명령"이라 안내하면
# LLM 이 ls/cat/nohup 을 시도하다 PowerShell 에서 실패한다(INT-1506).
if _IS_WINDOWS:
    _HOST_EXEC_DESC = (
        "호스트에서 직접 PowerShell 명령을 실행한다(cmd.exe 아님). "
        "파일 이동·복사·폴더 생성 등 권한이 필요한 작업에 사용. "
        "Move-Item, Copy-Item, New-Item, Rename-Item, Get-ChildItem, Get-Content, "
        "Start-Process 등은 allowlist에 있어 바로 실행된다. "
        "그 외 명령은 ask='on-miss'(기본) 모드에서 사용자 승인 요청을 반환한다. "
        "승인 요청이 반환되면 사용자에게 명령을 보여주고 확인을 받은 뒤 ask='off'로 재호출한다. "
        "bash 관용구(ls/cat/rm/nohup/~)가 아니라 PowerShell cmdlet을 쓸 것. "
        "삭제는 Remove-Item이 영구삭제이므로 신중히 — 재귀 루트/홈 삭제·Format-Volume은 항상 차단."
    )
    _HOST_EXEC_CMD_DESC = "실행할 PowerShell 명령. 경로는 절대경로 또는 $HOME 사용."
    _HOST_EXEC_TIMEOUT_DESC = "타임아웃(초). 기본 300초(5분). 장시간 작업은 Start-Job 백그라운드 권장."
else:
    _HOST_EXEC_DESC = (
        "호스트에서 직접 bash 명령을 실행한다. "
        "iCloud Drive 파일 이동·복사·폴더 생성처럼 TCC 권한이 필요한 작업에 사용. "
        "mv, mkdir, cp, osascript, open 명령은 allowlist에 있어 바로 실행된다. "
        "그 외 명령은 ask='on-miss'(기본) 모드에서 사용자 승인 요청을 반환한다. "
        "승인 요청이 반환되면 사용자에게 명령을 보여주고 확인을 받은 뒤 "
        "ask='off'로 재호출한다. "
        "rm은 자동으로 trash로 치환된다. 위험한 명령(rm -rf /, mkfs 등)은 항상 차단."
    )
    _HOST_EXEC_CMD_DESC = "실행할 bash 명령. 경로는 절대경로 또는 ~ 사용."
    _HOST_EXEC_TIMEOUT_DESC = "타임아웃(초). 기본 300초(5분). 5분 초과 작업은 nohup ... & 백그라운드 실행 권장."

CODE_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "host_exec",
        "description": _HOST_EXEC_DESC,
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": _HOST_EXEC_CMD_DESC,
                },
                "ask": {
                    "type": "string",
                    "enum": ["on-miss", "off", "always"],
                    "default": "on-miss",
                    "description": (
                        "on-miss: allowlist 미매치 시만 승인 요청(기본). "
                        "off: 승인 없이 바로 실행(사용자 확인 후 재호출 시 사용). "
                        "always: 항상 승인 요청."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "default": 300,
                    "description": _HOST_EXEC_TIMEOUT_DESC,
                },
            },
            "required": ["command"],
        },
    },
    {
        "type": "function",
        "name": "bash_exec",
        "description": (
            "bash 명령어를 격리된 샌드박스 컨테이너에서 실행한다. "
            "파일 조작, 데이터 처리, 계산 등에 사용. "
            "경로는 호스트 표기 그대로 쓰면 된다 — ~/dev/VEGA/data, "
            "/Users/<me>/... 같은 호스트 경로는 컨테이너 경로로 자동 변환된다 "
            "(VEGA data→/vega_data 읽기/쓰기, 그 외 홈→/host_home 읽기 전용). "
            "호스트 파일을 가공하려면 /vega_data로 복사한 뒤 작업할 것. "
            "인터넷 접근 없음. 호스트 시스템 상태(프로세스·Docker)를 보려면 system_info 사용."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "실행할 bash 명령어"},
                "timeout": {"type": "integer", "default": 60, "description": "타임아웃(초)"},
            },
            "required": ["command"],
        },
    },
    {
        "type": "function",
        "name": "python_exec",
        "description": (
            "Python 코드를 격리된 샌드박스 컨테이너에서 실행한다. "
            "데이터 분석, 계산, 파일 처리 등에 사용. "
            "차트가 필요하면 chart_matplotlib 또는 chart_plotly를 대신 사용. "
            "경로는 호스트 표기 그대로 쓰면 된다 — ~/dev/VEGA/data, "
            "/Users/<me>/... 같은 호스트 경로는 컨테이너 경로로 자동 변환된다 "
            "(VEGA data→/vega_data 읽기/쓰기, 그 외 홈→/host_home 읽기 전용). "
            "호스트 파일을 수정·생성하려면 /vega_data 아래에서 작업할 것."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "실행할 Python 코드"},
                "timeout": {"type": "integer", "default": 60, "description": "타임아웃(초)"},
            },
            "required": ["code"],
        },
    },
    {
        "type": "function",
        "name": "chart_matplotlib",
        "description": (
            "matplotlib으로 차트를 그려 화면에 표시한다. 샌드박스에서 실행. "
            "line, bar, scatter, histogram, heatmap 등 정적 차트에 적합. "
            "plt/np/pd는 import 없이 사용 가능."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "matplotlib 코드"},
            },
            "required": ["code"],
        },
    },
    {
        "type": "function",
        "name": "chart_plotly",
        "description": (
            "plotly로 인터랙티브 차트를 그려 화면에 표시한다. 샌드박스에서 실행. "
            "hover, zoom, 애니메이션이 필요한 차트에 적합. "
            "fig = px.xxx(...) 또는 fig = go.Figure(...) 형태로 fig를 정의해야 함."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "plotly 코드 (fig 변수 필수)"},
            },
            "required": ["code"],
        },
    },
    {
        "type": "function",
        "name": "system_info",
        "description": (
            "호스트 시스템 상태를 조회한다 — CPU, 메모리, 디스크, 실행 중인 Docker 컨테이너. "
            "샌드박스가 아닌 호스트에서 직접 실행."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "sandbox_save_module",
        "description": (
            "Python 코드를 샌드박스 영속 모듈로 저장한다 (/workspace/lib/<name>.py). "
            "저장 후 다음 python_exec에서 바로 import 가능. "
            "재시작 후에도 유지되는 '에이전트 스킬 라이브러리'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "module_name": {"type": "string", "description": "모듈 이름 (예: data_utils, finance_tools)"},
                "code": {"type": "string", "description": "저장할 Python 코드"},
            },
            "required": ["module_name", "code"],
        },
    },
    {
        "type": "function",
        "name": "sandbox_list_skills",
        "description": "샌드박스에 누적된 스킬 현황 조회: 저장된 모듈, pip 설치 패키지, 실행 이력 파일 목록.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "vega_reload_tools",
        "description": (
            "pipeline/ 도구 모듈을 서버 재시작 없이 핫리로드한다. "
            "도구 코드(tools_google.py, tools_code.py 등)를 수정한 뒤 반드시 호출한다. "
            "importlib.reload()로 서브모듈을 다시 로드하고 TOOL_FUNCTIONS/TOOL_SCHEMAS를 갱신한다."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


def _sandboxed_save_module(module_name: str, code: str) -> dict:
    from pipeline.sandbox import sandbox_save_module
    return sandbox_save_module(module_name, code)


def _sandboxed_list_skills() -> dict:
    from pipeline.sandbox import sandbox_list_skills
    return sandbox_list_skills()


def vega_reload_tools() -> dict:
    """
    Hot-reloads pipeline/ tool modules — no server restart needed.
    Reloads submodules via importlib.reload() and updates
    TOOL_FUNCTIONS / TOOL_SCHEMAS in-place.
    """
    import importlib
    import sys

    _targets = [
        "pipeline.tools_google",
        "pipeline.tools_imessage",
        "pipeline.tools_web",
        "pipeline.tools_things",
        "pipeline.tools_office",
        "pipeline.tools_code",
        "pipeline.tools_kis",
        "pipeline.tools",
    ]

    errors: list[str] = []
    reloaded: list[str] = []

    for mod_name in _targets:
        if mod_name not in sys.modules:
            continue
        try:
            importlib.reload(sys.modules[mod_name])
            reloaded.append(mod_name)
        except Exception as e:
            errors.append(f"{mod_name}: {e}")

    # Update TOOL_FUNCTIONS / TOOL_SCHEMAS in-place
    # (clear+update so references already imported by streaming.py etc. are reflected)
    try:
        import pipeline.tools as _t
        new_fns = dict(_t.TOOL_FUNCTIONS)
        new_schemas = list(_t.TOOL_SCHEMAS)
        _t.TOOL_FUNCTIONS.clear()
        _t.TOOL_FUNCTIONS.update(new_fns)
        _t.TOOL_SCHEMAS.clear()
        _t.TOOL_SCHEMAS.extend(new_schemas)
        tool_count = len(_t.TOOL_FUNCTIONS)
    except Exception as e:
        errors.append(f"tools update: {e}")
        tool_count = -1

    return {
        "ok": not errors,
        "reloaded": reloaded,
        "tool_count": tool_count,
        "errors": errors,
    }


CODE_TOOL_FUNCTIONS: dict = {
    "host_exec":           host_exec,
    "bash_exec":           _sandboxed_bash,
    "python_exec":         _sandboxed_python,
    "chart_matplotlib":    _sandboxed_matplotlib,
    "chart_plotly":        _sandboxed_plotly,
    "system_info":         system_info,
    "sandbox_save_module": _sandboxed_save_module,
    "sandbox_list_skills": _sandboxed_list_skills,
    "vega_reload_tools":   vega_reload_tools,
}
