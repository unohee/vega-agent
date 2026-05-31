# Created: 2026-05-31
# Purpose: PyInstaller spec — VEGA Core 백엔드 단일 바이너리 빌드 정의
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
    # Docker 코드 샌드박스 정의 — 설치본에서 ensure_sandbox_ready 가 compose build/up 하려면 필요
    (os.path.join(REPO_ROOT, "sandbox", "Dockerfile"), "sandbox"),
    (os.path.join(REPO_ROOT, "sandbox", "docker-compose.yml"), "sandbox"),
]

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
    excludes=[],
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
