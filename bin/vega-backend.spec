# Created: 2026-05-31
# Purpose: PyInstaller spec — VEGA Agent 백엔드 단일 바이너리 빌드 정의
# Dependencies: vega_backend_launcher.py, web/server.py, pipeline/*
# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

# SPECPATH 는 이 spec 파일이 있는 디렉터리(bin/) — 한 단계 위가 레포 루트
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = []
binaries = []
hiddenimports = []

# web/static (chat.html, dashboard.html, install_wizard.html) + data/ 기본값 번들
datas += [
    (os.path.join(REPO_ROOT, "web", "static"), "web/static"),
    (os.path.join(REPO_ROOT, "data", "agents"), "data/agents"),
    (os.path.join(REPO_ROOT, "data", "commands"), "data/commands"),
    (os.path.join(REPO_ROOT, "data", "llm_providers.json"), "data"),
    (os.path.join(REPO_ROOT, "data", "tool_groups.json"), "data"),
    (os.path.join(REPO_ROOT, "data", "mcp.json"), "data"),
    (os.path.join(REPO_ROOT, "data", "slack_oauth_client.json"), "data"),
    # Docker 코드 샌드박스 정의 — 설치본에서 ensure_sandbox_ready 가 compose build/up 하려면 필요
    (os.path.join(REPO_ROOT, "sandbox", "Dockerfile"), "sandbox"),
    (os.path.join(REPO_ROOT, "sandbox", "docker-compose.yml"), "sandbox"),
]

# Google OAuth 내장 client — gitignore라 CI(release-dmg.yml)가 GOOGLE_OAUTH_CLIENT_JSON
# 시크릿에서 복원한다. slack 과 달리 선택적(시크릿 없으면 파일도 없음)이라 조건부 포함.
# 누락 시 frozen 앱의 google.is_configured()가 False → 설정 창에서 "OAuth client 없음"
# 으로 떠 연결 버튼이 죽는다. slack 만 spec 에 있고 google 이 빠졌던 회귀를 막는다.
_google_client = os.path.join(REPO_ROOT, "data", "google_oauth_client.json")
if os.path.exists(_google_client):
    datas += [(_google_client, "data")]

# 배포 기본 키 (.env) — build_dmg.sh [pre] 단계가 repo .env에서 추출 생성.
# 번들 루트(".")에 실리면 keychain._env_file_paths의 repo-루트 폴백이
# frozen 앱에서 _MEIPASS/.env 로 이 파일을 찾는다. 없으면(수동 spec 빌드) 생략.
_bundle_env = os.path.join(REPO_ROOT, "bin", "bundle_env", ".env")
if os.path.exists(_bundle_env):
    datas += [(_bundle_env, ".")]

# FastAPI / uvicorn / starlette / MCP 서브모듈 (동적 import 보장)
# 주의: mcp 전체를 collect_submodules 하면 mcp.cli(typer 선택의존) 수집 중 죽는다.
#       런타임엔 mcp.cli 가 불필요하므로 mcp 는 핵심 하위만 명시 수집한다.
for pkg in ("uvicorn", "fastapi", "starlette", "anyio", "sse_starlette",
            "fastmcp", "httpx_sse", "httpx", "openai", "anthropic",
            "tenacity", "aiosqlite", "aiofiles",
            "mcp.client", "mcp.shared", "mcp.types", "mcp.server"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass
hiddenimports += ["mcp", "mcp.types"]

# 사무 문서 / 데이터 처리 라이브러리 — VEGA.app 단독(Docker 없이) PDF/XLS/DOCX/PPTX·차트 처리.
# tools_office.py 는 함수 안 + sandbox 문자열 코드에서 import 하므로 정적 분석이 못 잡는다.
#
# 주의: collect_submodules 는 쓰지 않는다. 빌드 환경(mlx_env)에 torch/transformers/
# tensorflow/pyarrow 등 거대 ML 패키지가 깔려 있어, pandas 등의 optional 백엔드를
# 타고 줄줄이 끌려와 번들이 789MB 로 폭발한다(2026-06-15 실측). 대신 최상위 패키지만
# hiddenimports 로 명시해 PyInstaller 가 실제 import 그래프만 따라가게 한다.
hiddenimports += [
    "openpyxl", "pypdf", "docx", "pptx", "msoffcrypto",
    "xlrd", "PIL", "numpy", "pandas", "matplotlib", "plotly", "mammoth",
]
# 데이터 파일 의존(matplotlib mpl-data, pptx 기본 템플릿)은 명시 수집.
# pandas 는 pyarrow 등 optional 백엔드를 데이터로 끌어올 수 있어 제외.
for pkg in ("matplotlib", "pptx", "openpyxl"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# 워크스페이스 도구 의존 — markdown(Superthread content HTML 변환; extension 을
# importlib 로 동적 로드해 정적 분석이 못 잡는다) + pyairtable(Airtable 도구; 함수 안
# lazy import). 둘 다 작은 순수 파이썬 패키지라 번들 폭발 없음 (INT-1498/1570/1571).
for pkg in ("markdown", "pyairtable"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# fastmcp 데이터 파일(스키마 등) 동봉
datas += collect_data_files("fastmcp", include_py_files=True)
try:
    datas += collect_data_files("mcp", include_py_files=True, excludes=["**/cli/**"])
except Exception:
    pass

# 패키지 메타데이터(.dist-info) 동봉 — fastmcp 등은 런타임에 importlib.metadata.version()
# 으로 자기 버전을 읽으므로 메타데이터가 없으면 PackageNotFoundError 로 죽는다.
for _meta_pkg in ("fastmcp", "mcp", "openai", "anthropic", "fastapi",
                  "starlette", "uvicorn", "pydantic", "httpx", "tiktoken"):
    try:
        datas += copy_metadata(_meta_pkg)
    except Exception:
        pass

# IANA 타임존 데이터(tzdata) — Windows 엔 시스템 tz 데이터베이스가 없어
# ZoneInfo("Asia/Seoul") 이 tzdata 패키지를 요구한다(없으면 import 시점 사망 — INT-1438).
# zoneinfo 가 tzdata 를 동적으로 찾으므로 hiddenimport 로 못박는다.
# macOS/Linux 빌드 venv 에 tzdata 가 없으면 그냥 건너뛴다(시스템 DB 사용).
try:
    datas += collect_data_files("tzdata")
    hiddenimports += collect_submodules("tzdata")
    datas += copy_metadata("tzdata")
except Exception:
    pass

# certifi CA 번들(cacert.pem) 명시 동봉 — SSL 검증의 신뢰 루트.
# PyInstaller 내장 hook 이 보통 cacert.pem 을 자동 동봉하지만, hook 누락/버전
# 변화에 대비해 명시적으로 못박는다. 이게 없으면 깨끗한 사용자 맥에서 외부 HTTPS 가
# CERTIFICATE_VERIFY_FAILED 로 죽는다(certifi.where() 가 가리키는 파일이 번들에 없음).
hiddenimports += ["certifi"]
try:
    datas += collect_data_files("certifi")  # cacert.pem
except Exception:
    pass
try:
    datas += copy_metadata("certifi")
except Exception:
    pass

# keyring: OAuth 토큰 저장 백엔드(Windows=Credential Manager, Linux=SecretService).
# 백엔드를 entry_points/동적 import 로 찾으므로 정적 분석이 못 잡는다 → 명시 수집
# (INT-1494). 없으면 frozen 앱에서 keyring 이 fail 백엔드로 떨어져 Windows OAuth
# 토큰 저장이 다시 깨진다. metadata 는 backend entry_points 등록에 필요.
try:
    hiddenimports += collect_submodules("keyring")
    datas += copy_metadata("keyring")
    # 동적으로만 참조되는 OS별 백엔드를 못박는다.
    hiddenimports += [
        "keyring.backends.Windows",      # WinVaultKeyring (Credential Manager)
        "keyring.backends.macOS",        # macOS Keychain (폴백 경로)
        "keyring.backends.SecretService",  # Linux
        "keyring.backends.fail",
        "win32ctypes.core",              # WinVaultKeyring 의존(pywin32-ctypes)
    ]
except Exception:
    pass

# 백엔드가 import 하는 자체 패키지 전부
hiddenimports += collect_submodules("pipeline")
hiddenimports += collect_submodules("web")
hiddenimports += collect_submodules("scripts")

# tiktoken 인코딩 데이터 (토큰 계산)
datas += collect_data_files("tiktoken_ext", include_py_files=True)
hiddenimports += collect_submodules("tiktoken_ext")
hiddenimports += ["tiktoken_ext", "tiktoken_ext.openai_public"]

a = Analysis(
    [os.path.join(REPO_ROOT, "bin", "vega_backend_launcher.py")],
    pathex=[REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 거대 ML 라이브러리 차단 — 빌드 환경(mlx_env)에 깔려 있고 pipeline 함수 안 lazy import
    # (memory_store.embed 의 mlx_lm 등)를 PyInstaller 가 정적으로 따라가 번들을 742MB+ 로
    # 부풀린다. 배포본은 EXAONE 모델이 없어 어차피 hash fallback 으로 동작하므로 빼도 안전
    # (memory_store.py:72). 사무/데이터 처리(numpy/pandas/openpyxl 등)엔 불필요 (2026-06-15).
    excludes=[
        "torch", "torchvision", "torchaudio", "transformers", "tensorflow",
        "tensorboard", "keras", "sklearn", "scikit_learn", "cv2", "opencv",
        "mlx", "mlx_lm", "sentence_transformers", "safetensors", "tokenizers",
        "scipy", "sympy", "numba", "llvmlite", "onnx", "onnxruntime",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="vega-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
