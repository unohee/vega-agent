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


def _chart_dir() -> Path:
    from pipeline.data_paths import charts_dir
    return charts_dir()


def _python_interp(script_path: str) -> list[str]:
    """Python 코드 실행에 쓸 인터프리터 커맨드 결정.

    배포본(PyInstaller frozen)은 동봉된 인터프리터를 `run-code`/`run-python`
    서브커맨드로 재사용한다 → Docker·시스템 Python 없이 동작 (vega_backend_launcher.py).
    개발 환경은 mlx_env, 둘 다 없으면 시스템 python3.
    검증: reference_frozen_interpreter_local_exec.md (2026-06-15)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "run-python", script_path]
    if MLX_PYTHON.exists():
        return [str(MLX_PYTHON), script_path]
    return [sys.executable, script_path]

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


def _ensure_workspace() -> Path:
    """App Support 워크스페이스(영속 개발 카탈로그) 보장 — 하위 디렉터리 + CATALOG seed.

    누적 카탈로그(INT-1870 §4b): VEGA가 만든 skills 모듈이 여기 쌓여 다음 실행에서 import 된다."""
    from pipeline.data_paths import workspace_dir
    ws = workspace_dir()
    for sub in ("skills", "site-packages", "history", "projects"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    cat = ws / "CATALOG.md"
    if not cat.exists():
        cat.write_text(
            "# VEGA Development Catalog\n\n"
            "VEGA가 만든 재사용 모듈/스크립트 목록. **새 도구를 만들기 전에 먼저 여기를 확인**하고\n"
            "이미 있으면 재사용한다(중복 양산 금지 — 메모리 큐레이션과 같은 원리).\n"
            "`skills/<name>.py` 는 코드 실행 시 `import <name>` 으로 불러올 수 있다.\n\n"
            "| module | 설명 | 추가일 |\n|---|---|---|\n",
            encoding="utf-8",
        )
    return ws


def _base_cwd() -> str:
    """Code execution working dir — session override > App Support workspace (catalog) > home.

    기본을 workspace 로 둬 VEGA 산출물·skills 가 App Support 에 누적 (INT-1870 §4b).
    상대 경로는 workspace 기준 — ~/Downloads 등은 `~/…` 또는 절대경로로 지정."""
    wd = _WORKING_DIR.get()
    if wd and Path(wd).expanduser().is_dir():
        return str(Path(wd).expanduser())
    try:
        from pipeline.data_paths import workspace_dir
        ws = workspace_dir()
        ws.mkdir(parents=True, exist_ok=True)
        return str(ws)
    except Exception:
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


# 시스템 파괴 — 호스트 직접 실행(Docker 격리 없음)에선 즉시 차단해야 한다.
# Docker 샌드박스 안에선 컨테이너 격리가 막아주지만, frozen 배포본은 호스트에서
# 직접 돌므로(reference_frozen_interpreter_local_exec.md) 이 가드가 마지막 방어선.
_PY_SYSTEM_DESTROY = _re.compile(
    r"os\.system\s*\(\s*['\"].*\brm\s+-\S*r"      # os.system("rm -rf ...")
    r"|subprocess\.(run|call|Popen|check_\w+)\s*\(.*\brm\b.*-\S*r"  # subprocess + rm -r
    r"|\bmkfs\b|\bdd\s+if=|>\s*/dev/sd",          # 디스크 포맷/덮어쓰기
    _re.IGNORECASE | _re.DOTALL,
)
# 홈/루트 통째 재귀 삭제 — rmtree(Path.home()), rmtree('/'), rmtree('~') 등.
# 하위 경로를 명시한 정상 삭제(rmtree('/tmp/foo'))는 막지 않는다.
_PY_DESTRUCTIVE_TREE = _re.compile(
    r"(shutil\.rmtree|os\.removedirs)\s*\(\s*"
    r"(['\"][~/]['\"]"                            # '/' 또는 '~' 리터럴
    r"|Path\.home\(\)\s*\)"                       # Path.home())
    r"|os\.path\.expanduser\(\s*['\"]~['\"]\s*\)\s*\))",  # expanduser('~'))
    _re.IGNORECASE,
)
# 시크릿 파일 쓰기/삭제 — 읽기뿐 아니라 변조도 차단.
_PY_SECRET_WRITE = _re.compile(
    r"(open\s*\(['\"][^'\"]*"
    r"(\.env\b|openai_oauth\.json|chatgpt_token\.json|client_secret[^'\"]*\.json"
    r"|id_rsa|id_ed25519|\.pem\b|\.key\b|credentials\.json|token\.json)"
    r"['\"]\s*,\s*['\"][wa]"                       # open(secret, 'w'/'a')
    r"|(os\.remove|os\.unlink|Path[^\n]*\.unlink)\s*\([^\n]*"
    r"(\.env\b|id_rsa|\.pem\b|\.key\b|credentials\.json|token\.json))",
    _re.IGNORECASE,
)


def _check_python_safeguards(code: str) -> str | None:
    """Python 코드의 파괴적/시크릿 작업 차단.

    호스트 직접 실행(frozen 배포본)은 Docker 시스템 격리가 없으므로 이 가드가
    마지막 방어선이다. 정상 파일 작업(openpyxl.save, shutil.copy2 백업 등)은
    막지 않고, 시스템 파괴·홈/루트 재귀삭제·시크릿 읽기/쓰기/삭제만 차단한다."""
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
    if _PY_SYSTEM_DESTROY.search(code):
        return "[SAFEGUARD] 차단: 시스템 파괴 작업(rm -rf / mkfs / dd / 디스크 덮어쓰기)."
    if _PY_DESTRUCTIVE_TREE.search(code):
        return "[SAFEGUARD] 차단: 홈/루트 디렉터리 통째 재귀 삭제. 하위 경로를 명시하세요."
    if _PY_SECRET_WRITE.search(code):
        return "[SAFEGUARD] 차단: 시크릿/키 파일 쓰기·삭제."
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
    # Windows cp1252 stdout 에서 한글/이모지 출력이 죽는 것 방지 (INT-1993).
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 개발 환경(mlx_env)에서만 venv 를 PATH 에 얹는다. 배포본엔 그 경로가 없다.
    if not getattr(sys, "frozen", False) and MLX_PYTHON.exists():
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

# 런타임 파일접근 가드 — 호스트 직접 실행(Docker 격리 없음)의 진짜 안전 경계.
# 정규식 정적 검사는 문자열분할/eval/__import__ 로 trivial 하게 우회된다(8중 7 우회,
# 2026-06-15 적대적 검증). 대신 builtins.open + os 파일함수를 실행 시점에 후킹하면
# 코드가 어떻게 난독화돼도 실제 파일 접근에서 잡힌다. 정책(allow/deny)은 path_guard
# 와 동일한 값을 JSON 으로 주입받아 재현. 읽기는 시크릿만, 쓰기/삭제는 정책 전체 적용.
_GUARD_PRELUDE = r"""
import builtins as _vb, os as _vos
_VG = __import__('json').loads({policy_json!r})
_ALLOW = _VG['allow']; _DENY_DIRS = set(_VG['deny_dirs'])
_DENY_NAMES = set(_VG['deny_names']); _DENY_SUFFIX = set(_VG['deny_suffix'])
_DENY_SUBSTR = _VG['deny_substr']; _DENY_PATHS = _VG['deny_paths']
def _vega_check(path, mode='r'):
    try:
        rp = _vos.path.realpath(_vos.path.expanduser(str(path)))
    except Exception:
        return
    name = _vos.path.basename(rp); nl = name.lower()
    parts = rp.split(_vos.sep)
    writing = any(m in str(mode) for m in ('w','a','x','+'))
    # 시크릿/키는 읽기·쓰기 모두 차단 (사용자가 못 푸는 하드 경계)
    if (name in _DENY_NAMES or nl in _DENY_NAMES or nl.startswith('.env')
            or any(d in parts for d in _DENY_DIRS)
            or any(nl.endswith(s) for s in _DENY_SUFFIX)
            or any(s in nl for s in _DENY_SUBSTR)):
        raise PermissionError('[SAFEGUARD] 시크릿/민감 파일 접근 차단: ' + rp)
    # 사용자 denylist (읽기·쓰기 모두)
    for d in _DENY_PATHS:
        if rp == d or rp.startswith(d + _vos.sep):
            raise PermissionError('[SAFEGUARD] 사용자 정책 차단 경로: ' + rp)
    # 허용 루트 밖 쓰기/삭제 차단 (읽기는 허용 — 자기 파일 읽기는 막지 않음)
    if writing and not any(rp == a or rp.startswith(a + _vos.sep) for a in _ALLOW):
        raise PermissionError('[SAFEGUARD] 허용 밖 경로 쓰기/삭제 차단: ' + rp)
_v_open = _vb.open
def _vg_open(file, mode='r', *a, **k):
    _vega_check(file, mode); return _v_open(file, mode, *a, **k)
_vb.open = _vg_open
for _fn in ('remove', 'unlink'):
    _orig = getattr(_vos, _fn)
    def _mk(o):
        def _w(path, *a, **k):
            _vega_check(path, 'w'); return o(path, *a, **k)
        return _w
    setattr(_vos, _fn, _mk(_orig))
_v_rmtree_mod = __import__('shutil')
_v_orig_rmtree = _v_rmtree_mod.rmtree
def _vg_rmtree(path, *a, **k):
    _vega_check(path, 'w'); return _v_orig_rmtree(path, *a, **k)
_v_rmtree_mod.rmtree = _vg_rmtree
""".strip()

_PYTHON_PRELUDE = """
import sys, os
from pathlib import Path
# VEGA module
sys.path.insert(0, '{vega_root}')
# Major project paths
for _p in ['{home}/dev/STONKS', '{home}/dev/ArtifactNet', '{home}/dev/template-crawler']:
    if _p not in sys.path and os.path.exists(_p):
        sys.path.insert(0, _p)
# VEGA 개발 카탈로그 — 이전 실행에서 만든 자작 모듈을 import 가능 (INT-1870 §4b)
for _p in ['{workspace}/skills', '{workspace}/site-packages']:
    if _p not in sys.path and os.path.exists(_p):
        sys.path.insert(0, _p)
os.chdir('{cwd}')
""".strip()


def _guard_prelude() -> str:
    """path_guard 정책을 직렬화해 런타임 가드 prelude 생성. 호스트 직접 실행 전용."""
    import json as _json
    try:
        from pipeline.path_guard import (
            _ALLOWED_ROOTS, _BLOCKED_DIRS, _BLOCKED_NAMES,
            _BLOCKED_SUFFIXES, _BLOCKED_SUBSTRINGS,
            _user_allow_roots, _user_deny_paths,
        )
        allow = [str(r) for r in _ALLOWED_ROOTS] + [str(r) for r in _user_allow_roots()]
        policy = {
            "allow": allow,
            "deny_dirs": list(_BLOCKED_DIRS),
            "deny_names": list(_BLOCKED_NAMES),
            "deny_suffix": list(_BLOCKED_SUFFIXES),
            "deny_substr": list(_BLOCKED_SUBSTRINGS),
            "deny_paths": [str(p) for p in _user_deny_paths()],
        }
    except Exception:
        # path_guard 로드 실패 시 최소 시크릿 차단만
        policy = {"allow": [str(Path.home()), "/tmp", "/private/tmp",
                            "/var/folders", "/private/var/folders"],
                  "deny_dirs": [".ssh", ".gnupg", ".aws"],
                  "deny_names": [".env", "id_rsa", "id_ed25519"],
                  "deny_suffix": [".pem", ".key"], "deny_substr": ["client_secret"],
                  "deny_paths": []}
    return _GUARD_PRELUDE.format(policy_json=_json.dumps(policy))


def python_exec(code: str, timeout: int = 60) -> dict:
    """
    Executes Python code (mlx_env) — home directory based, includes major project paths.
    Returns: {"stdout", "stderr", "returncode", "error"(if any)}
    """
    # 1차: 정규식 정적 검사 — 빠른 실수 방지(guardrail). 우회 가능하므로 경계 아님.
    err = _check_python_safeguards(code)
    if err:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": err}

    # 2차(진짜 경계): 런타임 파일접근 가드 prelude. open/os.remove/shutil.rmtree 후킹으로
    # 난독화 우회 코드도 실제 파일 접근 시점에 차단. Docker 격리 없는 호스트 실행의 방어선.
    from pipeline.data_paths import workspace_dir
    # 경로는 forward-slash 로 정규화한다 — Windows 의 백슬래시 경로(C:\Users\…)를 생성 코드
    # 문자열 리터럴에 그대로 박으면 '\U'(C:\Users) 등이 unicodeescape SyntaxError 를 낸다
    # (Windows CI pytest 실패, INT-1993). Windows 도 forward-slash 경로를 받아들인다.
    _fs = lambda p: str(p).replace("\\", "/")
    prelude = _guard_prelude() + "\n" + _PYTHON_PRELUDE.format(
        vega_root=_fs(VEGA_ROOT), home=_fs(Path.home()),
        cwd=_fs(_base_cwd()), workspace=_fs(workspace_dir())
    )
    full_code = prelude + "\n" + textwrap.dedent(code)

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False,
                                     encoding="utf-8") as f:
        f.write(full_code)
        tmppath = f.name

    env = os.environ.copy()
    # Windows: 자식 파이썬의 stdout 인코딩이 cp1252 면 한글·이모지 print 가
    # UnicodeEncodeError('charmap')로 죽는다(INT-1993). UTF-8 모드를 강제한다.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 개발 환경(mlx_env)에서만 venv 환경변수를 세팅한다. frozen 배포본은 동봉
    # 인터프리터가 자체 경로를 들고 있어 mlx_bin 주입이 불필요/유해.
    if not getattr(sys, "frozen", False) and MLX_PYTHON.exists():
        mlx_bin = str(MLX_PYTHON.parent)
        env["PATH"] = f"{mlx_bin}:{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = str(MLX_PYTHON.parent.parent)

    try:
        result = subprocess.run(
            _python_interp(tmppath),
            capture_output=True, text=True, timeout=timeout, env=env,
            encoding="utf-8", errors="replace",
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
    path = _chart_dir() / f"chart_{uuid.uuid4().hex[:8]}.png"
    return str(path)


def chart_matplotlib(code: str) -> dict:
    """
    Runs matplotlib code and saves output as PNG.
    Automatically saves any open figure; or call plt.savefig() directly in code.
    Returns: {"__type": "image", "path": str, "stdout": str} | {"error": str}
    """
    import uuid
    chart_path = str(_chart_dir() / f"chart_{uuid.uuid4().hex[:8]}.png")

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
    html_path = str(_chart_dir() / f"chart_{uuid.uuid4().hex[:8]}.html")

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
            "bash 명령어를 호스트에서 실행한다 (Docker 샌드박스 제거, INT-1870). "
            "파일 조작, 데이터 처리, 계산 등에 사용. "
            "경로는 호스트 절대경로 또는 ~/… (상대경로는 workspace 기준). "
            "세션 작업 폴더가 있으면 cwd 로 사용; 없으면 App Support workspace. "
            "path_guard 로 허용 경로만 읽기/쓰기. 인터넷은 web_search 등 별도 도구."
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
            "Python 코드를 호스트에서 실행한다 (Docker 샌드박스 제거, INT-1870). "
            "데이터 분석, 계산, 파일 처리 등에 사용. "
            "차트가 필요하면 chart_matplotlib 또는 chart_plotly를 대신 사용. "
            "경로는 호스트 절대경로 또는 ~/… (상대경로는 workspace 기준). "
            "세션 작업 폴더·workspace skills 가 PYTHONPATH 에 포함."
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
            "matplotlib으로 차트를 그려 PNG 로 저장·표시한다. 호스트에서 실행. "
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
            "plotly로 인터랙티브 차트를 그려 HTML 로 저장·표시한다. 호스트에서 실행. "
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
            "호스트 시스템 상태 — CPU, 메모리, 디스크, (설치 시) Docker 컨테이너 목록."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "sandbox_save_module",
        "description": (
            "Python 유틸리티를 App Support workspace/skills 에 저장한다. "
            "이후 python_exec 에서 import 가능 (INT-1870 §4b)."
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
        "description": "workspace/skills 에 누적된 모듈·패키지·실행 이력 조회 (sandbox_list_skills).",
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


def _catalog_add(ws: Path, name: str, code: str) -> None:
    """CATALOG.md 에 모듈 1줄 추가(이미 있으면 생략). 설명은 코드 첫 docstring/주석에서 추출."""
    cat = ws / "CATALOG.md"
    desc = ""
    for line in code.splitlines():
        s = line.strip().strip('"').strip("'").lstrip("#").strip()
        if s:
            desc = s[:80]
            break
    try:
        existing = cat.read_text(encoding="utf-8") if cat.exists() else ""
        if f"| `{name}` " in existing:
            return
        from datetime import date
        with cat.open("a", encoding="utf-8") as f:
            f.write(f"| `{name}` | {desc or '—'} | {date.today().isoformat()} |\n")
    except Exception:
        pass


def _sandboxed_save_module(module_name: str, code: str) -> dict:
    """자작 모듈을 App Support 워크스페이스 카탈로그(skills/)에 영속 저장 — 다음 실행에서
    import 가능. 호스트 영속(컨테이너 휘발 볼륨 아님), Docker 의존 없음 (INT-1870 §4b)."""
    safe = _re.sub(r"[^A-Za-z0-9_]", "", module_name) or "module"
    try:
        ws = _ensure_workspace()
        path = ws / "skills" / f"{safe}.py"
        path.write_text(code, encoding="utf-8")
        _catalog_add(ws, safe, code)
        return {"ok": True, "module": safe, "path": str(path)}
    except Exception as e:
        return {"error": str(e)}


def _sandboxed_list_skills() -> dict:
    """워크스페이스 카탈로그 조회 — skills 모듈·CATALOG·history. '만들기 전 먼저 확인' 용 (INT-1870 §4b)."""
    try:
        ws = _ensure_workspace()
        skills = sorted(p.stem for p in (ws / "skills").glob("*.py"))
        history = sorted(p.name for p in (ws / "history").glob("*.jsonl"))
        cat = ws / "CATALOG.md"
        catalog = cat.read_text(encoding="utf-8")[:4000] if cat.exists() else ""
        return {"ok": True, "workspace": str(ws), "skills": skills,
                "history": history, "catalog": catalog}
    except Exception as e:
        return {"error": str(e)}


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


# 코드 실행은 호스트 동봉 인터프리터로 일원화 (Docker 제거 — INT-1870 Phase C).
# 안전은 컨테이너 격리가 아니라 path_guard + _check_*_safeguards + _guard_prelude(런타임
# 파일접근 후킹) + trash 치환 + permission mode 로 보장한다.
CODE_TOOL_FUNCTIONS: dict = {
    "host_exec":           host_exec,
    "bash_exec":           bash_exec,
    "python_exec":         python_exec,
    "chart_matplotlib":    chart_matplotlib,
    "chart_plotly":        chart_plotly,
    "system_info":         system_info,
    "sandbox_save_module": _sandboxed_save_module,
    "sandbox_list_skills": _sandboxed_list_skills,
    "vega_reload_tools":   vega_reload_tools,
}
