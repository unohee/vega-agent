# Created: 2026-05-18
# Purpose: VEGA sandbox container management — persistent container exec approach
# Dependencies: docker (CLI), subprocess
# Test Status: untested

from __future__ import annotations

import datetime
import json
import re
import subprocess
import textwrap
import time
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

CONTAINER = "vega-sandbox"
IMAGE = "vega-sandbox:latest"
COMPOSE_DIR = Path(__file__).parent.parent / "sandbox"

# VEGA user data dir (컨테이너에 /vega_data 로 rw 마운트). data_paths 단일 출처 사용.
try:
    from pipeline.data_paths import data_dir as _data_dir
    VEGA_DATA = _data_dir()
except Exception:
    VEGA_DATA = Path(__file__).parent.parent / "data"
TIMEOUT_DEFAULT = 30

# Per-request working directory — set via set_sandbox_project_dir.
# When set, bash/python runs in an ephemeral container with that dir mounted as /project (rw).
import contextvars as _cv
_PROJECT_DIR: "_cv.ContextVar[str | None]" = _cv.ContextVar("vega_sandbox_project", default=None)
_PROJECT_MOUNT = "/project"  # working directory path inside the container


def set_sandbox_project_dir(path: str | None) -> None:
    """Set the working directory for the current execution context (None = use persistent container)."""
    _PROJECT_DIR.set(path or None)

# Host path → container path mapping.
# Allows agents to pass host paths (e.g. ~/dev/VEGA/data) directly;
# they are rewritten in command/code strings. VEGA data path is more specific, so it is substituted first.
_HOST_HOME = str(Path.home())
_VEGA_DATA_HOST = f"{_HOST_HOME}/dev/VEGA/data"
# (host notation, container path) — longer paths first
_PATH_MAP: list[tuple[str, str]] = [
    (_VEGA_DATA_HOST, "/vega_data"),
    ("~/dev/VEGA/data", "/vega_data"),
    ("$HOME/dev/VEGA/data", "/vega_data"),
    (_HOST_HOME, "/host_home"),
    ("~/", "/host_home/"),
    ("$HOME/", "/host_home/"),
]


def _rewrite_host_paths(text: str) -> str:
    """Rewrite host paths in commands/code to container paths.
    If a project dir is set, its absolute path is substituted to /project first."""
    proj = _PROJECT_DIR.get()
    if proj:
        proj_abs = str(Path(proj).expanduser())
        text = text.replace(proj_abs, _PROJECT_MOUNT)
    for host, container in _PATH_MAP:
        text = text.replace(host, container)
    return text


# ── Container state ───────────────────────────────────────────────────────────

def docker_available() -> bool:
    """Docker 데몬이 응답하는지 확인. 미설치/미기동이면 False (조용히 skip 용)."""
    try:
        r = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _container_running() -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _compose_env() -> dict:
    """compose 가 참조하는 경로 환경변수를 주입 — 배포본/사용자 환경 독립."""
    import os
    env = os.environ.copy()
    env.setdefault("VEGA_HOST_HOME", _HOST_HOME)
    env.setdefault("VEGA_DATA_DIR", str(VEGA_DATA))
    return env


def ensure_running() -> None:
    """컨테이너가 없거나 멈췄으면 기동. 이미지는 없을 때만 빌드(볼륨/영속 보존).
    이미 돌고 있으면 즉시 반환 — 매번 재구축하지 않는다."""
    if _container_running():
        return
    # docker compose up -d: 이미지 없으면 빌드, 있으면 재사용. 기존 named volume 보존.
    subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=str(COMPOSE_DIR), check=True,
        capture_output=True, env=_compose_env(),
    )
    for _ in range(30):
        if _container_running():
            return
        time.sleep(1)
    raise RuntimeError("vega-sandbox 컨테이너 기동 실패")


_LOW_MEM_BYTES = 16 * 1024 ** 3


def low_memory_host() -> bool:
    """16GB 미만 머신 여부 (INT-1430). Docker Desktop VM이 수 GB를 상주 점유하므로
    저사양에서는 샌드박스 선기동(warmup)을 생략한다 — 컨테이너는 sandbox_* 도구
    첫 호출 시(_exec → ensure_running) 온디맨드로만 띄운다. 판정 실패 시 False(기존 동작)."""
    try:
        import psutil
        return psutil.virtual_memory().total < _LOW_MEM_BYTES
    except Exception:
        return False


def ensure_sandbox_ready(timeout: float = 0) -> dict:
    """기동/설치 시 호출하는 자동 확보 진입점. Docker 가 있으면 컨테이너를 확보하고,
    없으면 조용히 skip한다(에러로 죽지 않음). 코드 실행 도구가 항상 준비되도록 한다.

    반환: {"ready": bool, "reason": str} — 호출부 로깅용."""
    if not docker_available():
        return {"ready": False, "reason": "docker_unavailable"}
    try:
        if _container_running():
            return {"ready": True, "reason": "already_running"}
        ensure_running()
        return {"ready": True, "reason": "started"}
    except Exception as e:
        return {"ready": False, "reason": f"start_failed: {e}"}


# ── Execution helpers ─────────────────────────────────────────────────────────

def _exec_project(cmd: list[str], project_dir: str, timeout: int = TIMEOUT_DEFAULT) -> dict:
    """Run in an ephemeral container with project_dir mounted as /project (rw).
    Persistent package/module volumes are also attached; cwd is set to /project.
    Same image and isolation as the persistent container (no network, no-new-privileges)."""
    p = Path(project_dir).expanduser()
    if not p.is_dir():
        return {"stdout": "", "stderr": "", "returncode": -1,
                "error": f"작업 폴더 없음: {project_dir}"}
    docker_cmd = [
        "docker", "run", "--rm",
        "-w", _PROJECT_MOUNT,
        "-v", f"{p}:{_PROJECT_MOUNT}:rw",
        "-v", "sandbox_sandbox_lib:/workspace/lib",
        "-v", "sandbox_sandbox_packages:/workspace/site-packages",
        "-v", f"{VEGA_DATA}:/vega_data:rw",
        "-v", f"{_HOST_HOME}:/host_home:ro",
        "-e", "PYTHONPATH=/workspace/lib:/workspace/site-packages",
        "-e", "PYTHONUNBUFFERED=1",
        "--network", "none",
        "--security-opt", "no-new-privileges:true",
        "--cpus", "2.0", "--memory", "2g",
        IMAGE,
    ] + cmd
    try:
        result = subprocess.run(
            docker_cmd, capture_output=True, text=True, timeout=timeout + 10,
        )
        return {
            "stdout": result.stdout[:6000],
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "", "returncode": -1,
                "error": f"타임아웃 ({timeout}초)"}
    except Exception as e:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": str(e)}


def _exec(cmd: list[str], timeout: int = TIMEOUT_DEFAULT) -> dict:
    """Run via docker exec → {stdout, stderr, returncode}.
    Routes to an ephemeral project container if a project dir is set."""
    proj = _PROJECT_DIR.get()
    if proj:
        return _exec_project(cmd, proj, timeout=timeout)
    ensure_running()
    try:
        result = subprocess.run(
            ["docker", "exec", CONTAINER] + cmd,
            capture_output=True, text=True, timeout=timeout + 5,
        )
        return {
            "stdout": result.stdout[:6000],
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "", "returncode": -1,
                "error": f"타임아웃 ({timeout}초)"}
    except Exception as e:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": str(e)}


# ── Public API ────────────────────────────────────────────────────────────────

# Detect pip install commands
_PIP_INSTALL_RE = re.compile(
    r"\bpip(?:3)?\s+install\s+(.*)"
)
# mlx_env Python pip (used for host-side wheel downloads)
_MLX_PIP = Path.home() / "dev/mlx_env/bin/pip"


def _intercept_pip(command: str) -> str | None:
    """
    Detect 'pip install <pkgs>' pattern → return package argument string.
    Returns None if the command is not a pip install.
    """
    m = _PIP_INSTALL_RE.search(command)
    if not m:
        return None
    # Pass through if --target/-t is already specified (manual re-invocation)
    args = m.group(1)
    if "--target" in args or "-t " in args:
        return None
    return args.strip()


def sandbox_pip_install(packages: str) -> dict:
    """
    pip install for the network-isolated sandbox:
    1. Download wheels to /vega_data/.pip_cache/ using the host mlx_env pip
       (/vega_data is a bind-mounted rw volume — accessible from macOS Docker Desktop)
    2. Install inside the container with pip install --no-index --find-links /vega_data/.pip_cache
       --target /workspace/site-packages
    """
    cache_dir = VEGA_DATA / ".pip_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 1. Download wheels on the host
    pkg_list = packages.split()
    dl = subprocess.run(
        [str(_MLX_PIP), "download",
         "--dest", str(cache_dir),
         "--python-version", "3.12",
         "--platform", "manylinux2014_aarch64",
         "--abi", "cp312",
         "--only-binary=:all:"] + pkg_list,
        capture_output=True, text=True, timeout=120,
    )
    if dl.returncode != 0:
        # Retry with pure-python wheel or source
        dl = subprocess.run(
            [str(_MLX_PIP), "download",
             "--dest", str(cache_dir)] + pkg_list,
            capture_output=True, text=True, timeout=120,
        )
        if dl.returncode != 0:
            return {"error": f"wheel 다운로드 실패: {dl.stderr[:400]}"}

    # 2. Install inside the container
    result = _exec(
        ["pip", "install", "--no-index",
         "--find-links", "/vega_data/.pip_cache",
         "--target", "/workspace/site-packages"] + pkg_list,
        timeout=60,
    )
    installed = [l for l in result.get("stdout", "").splitlines()
                 if "Successfully installed" in l or "already satisfied" in l.lower()]
    return {
        "returncode": result.get("returncode", -1),
        "stdout": "\n".join(installed) or result.get("stdout", "")[-300:],
        "stderr": result.get("stderr", "")[:200] if result.get("returncode") != 0 else "",
    }


def _rewrite_pip(command: str) -> tuple[str, bool]:
    """
    Detect pip install pattern.
    Returns: (command, is_pip_install)
    If is_pip_install=True, the caller must route to sandbox_pip_install().
    """
    pkgs = _intercept_pip(command)
    return (pkgs or command), (pkgs is not None)


def _record_history(kind: str, content: str, result: dict) -> None:
    """Append an execution record to /workspace/history/YYYYMMDD.jsonl."""
    try:
        ts = datetime.datetime.now(KST)
        record = {
            "ts": ts.isoformat(),
            "kind": kind,
            "content": content[:2000],
            "returncode": result.get("returncode"),
            "stdout_snippet": result.get("stdout", "")[:500],
            "error": result.get("error"),
        }
        day = ts.strftime("%Y%m%d")
        # Append directly to the file inside the container
        import base64 as _b64
        line = _b64.b64encode(json.dumps(record, ensure_ascii=False).encode()).decode()
        _exec(["bash", "-c",
               f"echo {line} | base64 -d >> /workspace/history/{day}.jsonl"],
              timeout=5)
    except Exception:
        pass  # history recording failure is non-fatal


def sandbox_bash(command: str, timeout: int = TIMEOUT_DEFAULT) -> dict:
    """
    Execute a bash command in the sandbox container.
    Home directory is /host_home (ro), VEGA data is /vega_data (rw).
    pip install is handled automatically via host-download → container-transfer (network-isolated sandbox).
    Returns: {stdout, stderr, returncode, error?}
    """
    pkgs_or_cmd, is_pip = _rewrite_pip(_rewrite_host_paths(command))
    if is_pip:
        result = sandbox_pip_install(pkgs_or_cmd)
    else:
        result = _exec(["bash", "-c", pkgs_or_cmd], timeout=timeout)
    _record_history("bash", command, result)
    return result


def sandbox_python(code: str, timeout: int = TIMEOUT_DEFAULT) -> dict:
    """
    Execute Python code in the sandbox container.
    /host_home (ro), /vega_data (rw), /workspace/lib + /workspace/site-packages (rw, on PYTHONPATH).
    Host paths in the code are automatically rewritten to container paths.
    Returns: {stdout, stderr, returncode, error?}
    """
    safe_code = _rewrite_host_paths(textwrap.dedent(code))
    import base64
    encoded = base64.b64encode(safe_code.encode()).decode()
    cmd = (
        f"echo {encoded} | base64 -d > /tmp/_vega_exec.py && "
        f"python /tmp/_vega_exec.py"
    )
    result = _exec(["bash", "-c", cmd], timeout=timeout)
    _record_history("python", code, result)
    return result


def sandbox_matplotlib(code: str, timeout: int = 30) -> dict:
    """
    Run matplotlib in the sandbox → save PNG to /vega_data/charts/.
    Returns: {"__type": "image", "path": str} | {"error": str}
    """
    import uuid
    chart_name = f"chart_{uuid.uuid4().hex[:8]}.png"
    # Path inside the container
    container_path = f"/vega_data/charts/{chart_name}"
    # Host path (read by Chainlit)
    host_path = str(VEGA_DATA / "charts" / chart_name)

    wrapper = f"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

{textwrap.dedent(code)}

_figs = [plt.figure(i) for i in plt.get_fignums()]
if _figs:
    plt.tight_layout()
    plt.savefig({repr(container_path)}, dpi=150, bbox_inches='tight')
    plt.close('all')
    print('__chart_path__:' + {repr(container_path)})
else:
    print('ERROR: figure 없음')
"""
    result = sandbox_python(wrapper, timeout=timeout)
    if result.get("error"):
        return {"error": result["error"]}
    if result["returncode"] != 0:
        return {"error": result["stderr"] or "실행 실패"}
    if Path(host_path).exists():
        return {"__type": "image", "path": host_path, "stdout": result["stdout"]}
    return {"error": "차트 파일 생성 실패\n" + result["stderr"]}


def sandbox_plotly(code: str, timeout: int = 30) -> dict:
    """
    Run plotly in the sandbox → save HTML to /vega_data/charts/.
    Returns: {"__type": "html", "path": str} | {"error": str}
    """
    import uuid
    chart_name = f"chart_{uuid.uuid4().hex[:8]}.html"
    container_path = f"/vega_data/charts/{chart_name}"
    host_path = str(VEGA_DATA / "charts" / chart_name)

    wrapper = f"""
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np

{textwrap.dedent(code)}

if 'fig' in dir():
    fig.write_html({repr(container_path)}, include_plotlyjs='cdn', full_html=True)
    print('__chart_path__:' + {repr(container_path)})
else:
    print('ERROR: fig 변수 없음')
"""
    result = sandbox_python(wrapper, timeout=timeout)
    if result.get("error"):
        return {"error": result["error"]}
    if result["returncode"] != 0:
        return {"error": result["stderr"] or "실행 실패"}
    if Path(host_path).exists():
        return {"__type": "html", "path": host_path}
    return {"error": "HTML 파일 생성 실패\n" + result["stderr"]}


def sandbox_save_module(module_name: str, code: str) -> dict:
    """
    Save Python code to /workspace/lib/<module_name>.py.
    The module can then be imported as 'import <module_name>' (included in PYTHONPATH).
    """
    import base64
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", module_name)
    encoded = base64.b64encode(textwrap.dedent(code).encode()).decode()
    result = _exec(
        ["bash", "-c",
         f"echo {encoded} | base64 -d > /workspace/lib/{safe_name}.py && echo saved"],
        timeout=10,
    )
    if result.get("returncode") == 0:
        return {"ok": True, "module": safe_name, "path": f"/workspace/lib/{safe_name}.py"}
    return {"error": result.get("stderr", "저장 실패")}


def sandbox_list_skills() -> dict:
    """
    Return the accumulated skill inventory in the sandbox:
    - /workspace/lib/*.py  — agent-created modules
    - /workspace/site-packages/ — pip-installed packages
    - /workspace/history/  — execution history file list
    """
    result = _exec(["bash", "-c", """
echo '=LIB='
ls /workspace/lib/*.py 2>/dev/null | xargs -I{} basename {} .py || echo '(없음)'
echo '=PACKAGES='
ls /workspace/site-packages 2>/dev/null | head -30 || echo '(없음)'
echo '=HISTORY='
ls /workspace/history 2>/dev/null || echo '(없음)'
"""], timeout=10)
    return {"ok": True, "info": result.get("stdout", "")}


def sandbox_status() -> dict:
    """Query container status and internal resource usage."""
    running = _container_running()
    if not running:
        return {"running": False}
    result = _exec([
        "bash", "-c",
        "echo '=CPU=' && cat /proc/loadavg && "
        "echo '=MEM=' && free -h | grep Mem && "
        "echo '=DISK=' && df -h /workspace | tail -1"
    ], timeout=10)
    return {"running": True, "info": result.get("stdout", "")}
