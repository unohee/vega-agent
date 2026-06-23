# Created: 2026-05-18
# Purpose: VEGA sandbox container management — persistent container exec approach
# Dependencies: docker (CLI), subprocess
# Test Status: untested

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
import textwrap
import time
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

CONTAINER = "vega-sandbox"
IMAGE = "vega-sandbox:latest"


def _resolve_compose_dir() -> Path:
    """sandbox/ (Dockerfile·docker-compose.yml) 디렉터리 해석.

    frozen 앱(noarchive=False)에선 이 모듈이 PYZ 안에 압축돼 __file__ 이 디스크의
    _MEIPASS/pipeline/sandbox.py 를 가리키지 않는다 → __file__.parent.parent/sandbox
    추정은 실존하지 않는 경로가 되어 `docker compose up` 의 cwd 가 깨진다. launcher 가
    명시 설정하는 VEGA_BUNDLE_ROOT(=sys._MEIPASS)를 우선 본다. spec 은 sandbox/ 를
    _MEIPASS/sandbox 로 번들한다. (INT-1505 keychain __file__ 함정과 동형)
    """
    import os as _os
    bundle_root = _os.environ.get("VEGA_BUNDLE_ROOT", "").strip()
    if bundle_root:
        cand = Path(bundle_root) / "sandbox"
        if (cand / "docker-compose.yml").exists():
            return cand
    return Path(__file__).parent.parent / "sandbox"


COMPOSE_DIR = _resolve_compose_dir()

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
# VEGA_DATA를 기준으로 경로맵을 구성 — ~/dev/VEGA/data 하드코딩 제거 (INT-1522)
_VEGA_DATA_HOST = str(VEGA_DATA)
# (host notation, container path) — longer paths first
_PATH_MAP: list[tuple[str, str]] = [
    (_VEGA_DATA_HOST, "/vega_data"),
    (_HOST_HOME, "/host_home"),
    ("~/", "/host_home/"),
    ("$HOME/", "/host_home/"),
]


def _rewrite_host_paths(text: str) -> str:
    """Rewrite host paths in commands/code to container paths.

    연결 폴더(_PROJECT_DIR)가 설정된 경우 — 권한 경계 모드(INT-1470). 컨테이너는
    연결 폴더(/project)와 VEGA data(/vega_data)만 본다. 홈 전체는 마운트하지 않으므로
    홈→/host_home 매핑도 하지 않는다 — 연결 폴더 밖 호스트 경로는 그대로 남아
    컨테이너에서 "없는 경로"로 자연히 실패한다(= 경계 밖 접근 차단).

    연결 폴더가 없으면(레거시 영속 컨테이너 경로) 기존 _PATH_MAP 매핑을 유지한다."""
    proj = _PROJECT_DIR.get()
    if proj:
        proj_abs = str(Path(proj).expanduser())
        text = text.replace(proj_abs, _PROJECT_MOUNT)
        text = text.replace(_VEGA_DATA_HOST, "/vega_data")
        return text
    for host, container in _PATH_MAP:
        text = text.replace(host, container)
    return text


# ── Container state ───────────────────────────────────────────────────────────

# Docker 부재 시 사용자에게 보여줄 메시지 (INT-1459).
# raw FileNotFoundError("[Errno 2] ... 'docker'")가 도구 결과로 노출되지 않게 한다.
_DOCKER_MISSING_MSG = (
    "Docker가 설치되어 있지 않아 샌드박스 코드 실행(bash/python)을 사용할 수 없습니다. "
    "OrbStack(https://orbstack.dev) 또는 Docker Desktop"
    "(https://www.docker.com/products/docker-desktop/)을 설치한 뒤 다시 시도해주세요. "
    "채팅·메모리·워크스페이스 연동 등 나머지 기능은 정상 작동합니다."
)
_DOCKER_DOWN_MSG = (
    "Docker는 설치되어 있지만 데몬이 실행 중이 아닙니다. "
    "OrbStack 또는 Docker Desktop 앱을 실행한 뒤 다시 시도해주세요."
)

# 'ok' 판정만 짧게 캐시 — 정상 경로에서 매 도구 호출마다 `docker info`(수십 ms)를
# 반복하지 않기 위함. 부정 판정(missing/down)은 캐시하지 않아, 사용자가 Docker를
# 설치/기동한 직후의 재시도가 즉시 감지된다.
_DOCKER_OK_TTL = 60.0
_docker_ok_until = 0.0


def docker_state() -> str:
    """Docker 가용성 3분류: 'ok' | 'missing'(CLI 미설치) | 'down'(데몬 미기동).
    which()는 매번 재평가(싸다) — 설치 후 재시도하면 바로 감지."""
    global _docker_ok_until
    if shutil.which("docker") is None:
        return "missing"
    if time.monotonic() < _docker_ok_until:
        return "ok"
    try:
        r = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return "down"
    if r.returncode == 0:
        _docker_ok_until = time.monotonic() + _DOCKER_OK_TTL
        return "ok"
    return "down"


def docker_available() -> bool:
    """Docker 데몬이 응답하는지 확인. 미설치/미기동이면 False (조용히 skip 용)."""
    return docker_state() == "ok"


def docker_opt_in() -> bool:
    """Docker 격리는 명시 opt-in 일 때만 사용한다 — 호스트 우선이 기본 (INT-1870).

    VEGA_USE_DOCKER 가 1/true/yes/on 이면 활성. 미설정(기본)이면 코드 실행은
    호스트 동봉 인터프리터로 직행한다 — Docker가 떠 있어도 자동으로 끌려가지 않는다
    (데몬이 home 을 /host_home 읽기전용으로 마운트해 ~/ 쓰기가 깨지던 문제 방지)."""
    import os
    return os.environ.get("VEGA_USE_DOCKER", "").strip().lower() in ("1", "true", "yes", "on")


def docker_enabled() -> bool:
    """실행 라우팅 결정용 — opt-in 됐고 데몬도 살아있을 때만 True.

    라우팅 분기(_docker_or_host / _office_exec)는 이 함수를 쓴다. docker_available 은
    상태 표시·설치 마법사 등 비라우팅 용도로 남긴다."""
    return docker_opt_in() and docker_available()


def _docker_error(state: str) -> dict:
    """missing/down 상태를 사용자 친화 에러 dict로 (도구 결과 포맷과 동일 스키마)."""
    msg = _DOCKER_MISSING_MSG if state == "missing" else _DOCKER_DOWN_MSG
    return {"stdout": "", "stderr": "", "returncode": -1,
            "error": msg, "docker": state, "sandbox_disabled": True}


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


# ── Docker 자동 설치 헬퍼 ────────────────────────────────────────────────────

def _brew_path() -> str | None:
    """Homebrew 실행 파일 경로. 없으면 None."""
    for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if Path(p).exists():
            return p
    return shutil.which("brew")


def brew_available() -> bool:
    return _brew_path() is not None


def _osascript_sudo(cmd: str) -> subprocess.CompletedProcess:
    """GUI 패스워드 팝업으로 sudo 명령 실행 (macOS only)."""
    script = f'do shell script "{cmd}" with administrator privileges'
    return subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True,
    )


def install_homebrew_iter():
    """Homebrew 설치 진행 — 각 단계를 (ok, message) 제너레이터로 스트리밍."""
    if brew_available():
        yield True, "Homebrew 이미 설치됨"
        return
    yield True, "Homebrew 설치 시작 (관리자 권한 요청)…"
    install_cmd = (
        '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    )
    r = _osascript_sudo(install_cmd)
    if r.returncode != 0:
        yield False, f"Homebrew 설치 실패: {r.stderr.strip() or r.stdout.strip()}"
        return
    yield True, "Homebrew 설치 완료"


def install_docker_iter():
    """Docker Desktop 설치 진행 — (ok, message) 제너레이터."""
    import platform
    system = platform.system()

    if docker_state() != "missing":
        yield True, "Docker 이미 설치됨"
        return

    if system == "Darwin":
        brew = _brew_path()
        if not brew:
            yield False, "Homebrew가 없어 Docker를 설치할 수 없습니다"
            return
        yield True, "Docker Desktop 설치 중… (수 분 소요될 수 있습니다)"
        r = subprocess.run(
            [brew, "install", "--cask", "docker"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            err = r.stderr.strip() or r.stdout.strip()
            yield False, f"Docker Desktop 설치 실패: {err}"
            return
        yield True, "Docker Desktop 설치 완료"

    elif system == "Windows":
        yield True, "winget으로 Docker Desktop 설치 중…"
        r = subprocess.run(
            ["winget", "install", "--id", "Docker.DockerDesktop",
             "-e", "--accept-source-agreements", "--accept-package-agreements"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            yield False, f"Docker Desktop 설치 실패: {r.stderr.strip()}"
            return
        yield True, "Docker Desktop 설치 완료"

    else:
        yield False, f"지원하지 않는 플랫폼: {system}"


def launch_docker_desktop_iter():
    """Docker Desktop 실행 + 데몬 응답 대기 — (ok, message) 제너레이터."""
    import platform
    if docker_state() == "ok":
        yield True, "Docker 데몬 이미 실행 중"
        return

    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", "-a", "Docker"])
    elif system == "Windows":
        import os
        docker_exe = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"
        if docker_exe.exists():
            subprocess.Popen([str(docker_exe)])

    yield True, "Docker Desktop 실행 중… 데몬 시작 대기 (최대 60초)"
    for i in range(60):
        time.sleep(2)
        if docker_state() == "ok":
            yield True, "Docker 데몬 준비 완료"
            return
        if i % 5 == 4:
            yield True, f"대기 중… ({(i+1)*2}초)"
    yield False, "Docker 데몬이 60초 내에 시작되지 않았습니다. Docker Desktop을 직접 실행해 주세요."


_SANDBOX_IMAGE = "ghcr.io/unohee/vega-sandbox:latest"


def image_ready() -> bool:
    """로컬에 vega-sandbox 이미지가 존재하면 True. docker 없으면 False."""
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", _SANDBOX_IMAGE],
            capture_output=True,
        )
        return r.returncode == 0
    except Exception:
        return False


def _ensure_image() -> None:
    """이미지가 로컬에 없으면 GHCR에서 pull. 이미 있으면 즉시 반환."""
    r = subprocess.run(
        ["docker", "image", "inspect", _SANDBOX_IMAGE],
        capture_output=True,
    )
    if r.returncode == 0:
        return
    subprocess.run(
        ["docker", "pull", _SANDBOX_IMAGE],
        check=True,
    )


def ensure_running() -> None:
    """컨테이너가 없거나 멈췄으면 기동. 이미지가 없으면 GHCR에서 pull.
    이미 돌고 있으면 즉시 반환 — 매번 재기동하지 않는다."""
    if _container_running():
        return
    _ensure_image()
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


def windows_docker_backend() -> dict:
    """Windows 에서 Docker Desktop 백엔드(WSL2 / Hyper-V) 가용성 점검 (INT-1505).

    Docker Desktop 은 WSL2 또는 Hyper-V 가 있어야 동작한다. docker 가 없을 때
    "무엇을 먼저 켜야 하는지"를 온보딩 UI 에 정확히 안내하려는 진단용. Windows 가
    아니면 빈 dict 를 반환한다(해당 없음).

    반환(Windows): {"wsl": bool|None, "hyperv": bool|None, "virtualization": bool|None}
      각 값 None = 점검 실패/불명. 진단 힌트일 뿐 하드 게이트가 아니다.
    """
    import platform
    if platform.system() != "Windows":
        return {}

    def _ok(args: list[str]) -> bool | None:
        try:
            r = subprocess.run(args, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=8)
            return r.returncode == 0
        except Exception:
            return None

    # WSL: `wsl --status` 가 성공하면 WSL 설치됨
    wsl = _ok(["wsl", "--status"])
    # Hyper-V / 가상화: PowerShell 로 기능 활성 여부 조회 (관리자 아니어도 조회는 가능)
    hyperv = None
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All).State"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12,
        )
        if r.returncode == 0:
            hyperv = "Enabled" in (r.stdout or "")
    except Exception:
        hyperv = None
    # CPU 가상화 활성(펌웨어) — systeminfo 의 가상화 항목
    virt = None
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12,
        )
        if r.returncode == 0:
            virt = "True" in (r.stdout or "")
    except Exception:
        virt = None

    return {"wsl": wsl, "hyperv": hyperv, "virtualization": virt}


def ensure_sandbox_ready(timeout: float = 0) -> dict:
    """기동/설치 시 호출하는 자동 확보 진입점. Docker 가 있으면 컨테이너를 확보하고,
    없으면 조용히 skip한다(에러로 죽지 않음). 코드 실행 도구가 항상 준비되도록 한다.

    반환: {"ready": bool, "reason": str} — 호출부 로깅용."""
    state = docker_state()
    if state != "ok":
        # docker_missing(미설치) / docker_down(데몬 미기동) 구분 — UI/로그 진단용 (INT-1459)
        return {"ready": False, "reason": f"docker_{state}"}
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
    # 권한 경계(INT-1470): 연결 폴더가 설정된 요청은 그 폴더(/project)와 VEGA
    # data(/vega_data)만 마운트한다. 홈 전체(/host_home)는 마운트하지 않는다 —
    # "위험해서 격리한다면서 홈 전체를 노출"하던 자가무효화(sandbox.py 옛 설계)를 제거.
    docker_cmd = [
        "docker", "run", "--rm",
        "-w", _PROJECT_MOUNT,
        "-v", f"{p}:{_PROJECT_MOUNT}:rw",
        "-v", "sandbox_sandbox_lib:/workspace/lib",
        "-v", "sandbox_sandbox_packages:/workspace/site-packages",
        "-v", f"{VEGA_DATA}:/vega_data:rw",
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
    # Docker 미설치/미기동이면 크래시 대신 친화 에러 반환 (INT-1459).
    # 모든 sandbox_* 도구가 이 함수를 거치므로 여기 한 곳에서 게이트한다.
    state = docker_state()
    if state != "ok":
        return _docker_error(state)
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
    # Docker 부재 시 wheel 다운로드 전에 바로 안내 (아래 _exec의 error dict는
    # 이 함수의 반환 조립에서 유실되므로 여기서 선제 차단) — INT-1459
    state = docker_state()
    if state != "ok":
        return _docker_error(state)

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
    import re as _re
    # /vega_data 하위 rm -rf 는 호스트 데이터 직접 삭제 — 차단(INT-1522).
    if _re.search(r"\brm\b.*(/vega_data|/host_home)", command):
        return {"error": "[SAFEGUARD] 샌드박스에서 /vega_data 또는 /host_home 직접 삭제는 차단됩니다."}
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
    state = docker_state()
    if state != "ok":
        # docker CLI 호출(_container_running) 전에 차단 — FileNotFoundError 방지 (INT-1459)
        return {"running": False, "docker": state,
                "error": _DOCKER_MISSING_MSG if state == "missing" else _DOCKER_DOWN_MSG}
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
