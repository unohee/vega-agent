#!/bin/bash
# VEGA macOS .dmg 빌드 스크립트 (아키텍처별 별도 DMG)
#
# 산출물:
#   build_output/VEGA-<ver>-aarch64.dmg  — Apple Silicon 전용
#   build_output/VEGA-<ver>-x86_64.dmg   — Intel Mac 전용
#
# 각 DMG는 해당 아키텍처 전용 PyInstaller 바이너리 + Tauri 빌드.
# mlx_env(torch/MLX 포함 6GB+) 대신 arm64_vega_env(최소 패키지)를 사용해
# 빌드 크기를 대폭 줄인다.
#
# venv 사전 요건:
#   arm64: ~/dev/arm64_vega_env  (python3 -m venv)
#   x86_64: ~/dev/intel64_env    (arch -x86_64 /usr/local/bin/python3-intel64 -m venv)
#   둘 다: pip install pyinstaller uvicorn fastapi starlette anyio sse-starlette \
#           fastmcp httpx openai anthropic tenacity aiosqlite aiofiles tiktoken certifi pydantic aiohttp
#
# 빌드 대상 선택:
#   bash scripts/build_dmg.sh          → 두 아키텍처 모두
#   VEGA_ARCH=aarch64 bash ...          → arm64만
#   VEGA_ARCH=x86_64  bash ...          → x86_64만

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="0.1.10"
APP_NAME="VEGA"
SIGN_APP="Developer ID Application: Heewon Oh (635QK74RYK)"
BUILD_DIR="$REPO_ROOT/build_output"
UPDATER_DIR="$BUILD_DIR/updater"

UPDATER_KEY_PATH="${TAURI_SIGNING_PRIVATE_KEY_PATH:-$HOME/.tauri/vega-updater.key}"
if [ -f "$UPDATER_KEY_PATH" ]; then
    export TAURI_SIGNING_PRIVATE_KEY="$(cat "$UPDATER_KEY_PATH")"
    export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}"
fi
unset APPLE_SIGNING_IDENTITY || true

BUILD_ARCH="${VEGA_ARCH:-all}"  # all | aarch64 | x86_64

# ── 아키텍처별 빌드 함수 ──────────────────────────────────────────────────────
build_arch() {
    local ARCH="$1"  # aarch64 | x86_64
    local RUST_TARGET PYINSTALLER PYINSTALLER_ARCH_CMD DMG_OUT TAURI_APP DMG_STAGE

    if [ "$ARCH" = "aarch64" ]; then
        RUST_TARGET="aarch64-apple-darwin"
        PYINSTALLER="${VEGA_ARM_VENV:-$HOME/dev/arm64_vega_env}/bin/pyinstaller"
        PYINSTALLER_ARCH_CMD=""
    else
        RUST_TARGET="x86_64-apple-darwin"
        PYINSTALLER="${VEGA_X86_VENV:-$HOME/dev/intel64_env}/bin/pyinstaller"
        PYINSTALLER_ARCH_CMD="arch -x86_64"
    fi

    DMG_OUT="$BUILD_DIR/${APP_NAME}-${VERSION}-${ARCH}.dmg"
    DMG_STAGE="$BUILD_DIR/dmg_stage_${ARCH}"
    TAURI_APP="$REPO_ROOT/desktop/target/${RUST_TARGET}/release/bundle/macos/${APP_NAME}.app"

    echo ""
    echo "══════════════════════════════════════════"
    echo "  빌드: $ARCH ($RUST_TARGET)"
    echo "══════════════════════════════════════════"

    # 0. PyInstaller
    if [ ! -x "$PYINSTALLER" ]; then
        echo "  ERROR: PyInstaller 없음: $PYINSTALLER" >&2; exit 1
    fi
    echo "[0] PyInstaller — vega-backend ($ARCH)..."
    $PYINSTALLER_ARCH_CMD "$PYINSTALLER" bin/vega-backend.spec \
        --distpath "bin/dist_${ARCH}" \
        --workpath "bin/build_pyinstaller_${ARCH}" \
        --noconfirm 2>&1 | grep -E "(ERROR|INFO: Building EXE|INFO: Build complete)" || true
    if [ ! -f "bin/dist_${ARCH}/vega-backend" ]; then
        echo "  ERROR: vega-backend ($ARCH) 빌드 실패" >&2; exit 1
    fi
    # Tauri externalBin은 타겟 suffix 파일을 요구
    cp "bin/dist_${ARCH}/vega-backend" "bin/vega-backend-${RUST_TARGET}"
    chmod +x "bin/vega-backend-${RUST_TARGET}"
    echo "  ✓ vega-backend ($(du -sh "bin/dist_${ARCH}/vega-backend" | cut -f1))"

    # 1. Tauri 빌드
    echo "[1] cargo tauri build ($ARCH)..."
    cd "$REPO_ROOT/desktop"
    cargo tauri build --target "$RUST_TARGET" --bundles app 2>&1 | grep -E "Compiling|Finished|error\[|Bundling"
    cd "$REPO_ROOT"
    if [ ! -d "$TAURI_APP" ]; then
        echo "  ERROR: VEGA.app ($ARCH) 없음" >&2; exit 1
    fi
    echo "  ✓ VEGA.app ($ARCH)"

    # 1.5. 재서명
    echo "[1.5] 재서명 (Developer ID + entitlements)..."
    VEGA_SIGN_ID="$SIGN_APP" VEGA_NOTARY_PROFILE="" \
        bash "$REPO_ROOT/scripts/sign_and_notarize.sh" "$TAURI_APP"

    # 2. DMG 스테이징
    echo "[2] DMG 스테이징..."
    mkdir -p "$BUILD_DIR"
    rm -rf "$DMG_STAGE"; mkdir -p "$DMG_STAGE"
    cp -R "$TAURI_APP" "$DMG_STAGE/"
    ln -s /Applications "$DMG_STAGE/Applications"
    echo "  ✓ $(du -sh "$DMG_STAGE/${APP_NAME}.app" | cut -f1)"

    # 3. DMG 생성
    echo "[3] hdiutil — DMG 생성..."
    rm -f "$DMG_OUT"
    hdiutil create -volname "VEGA ${VERSION}" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG_OUT"
    echo "  ✓ DMG 생성"

    # 4. DMG 서명 + 공증
    echo "[4] DMG 서명/공증..."
    VEGA_SIGN_ID="$SIGN_APP" bash "$REPO_ROOT/scripts/sign_and_notarize.sh" --artifact-only "$DMG_OUT"

    # 4.5. updater 아티팩트
    if [ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
        echo "[4.5] updater 아티팩트 생성..."
        mkdir -p "$UPDATER_DIR"
        local UPDATER_TGZ="$UPDATER_DIR/${APP_NAME}-${VERSION}-${ARCH}.app.tar.gz"
        tar -C "$(dirname "$TAURI_APP")" -czf "$UPDATER_TGZ" "$(basename "$TAURI_APP")"
        ( cd "$REPO_ROOT/desktop" && cargo tauri signer sign "$UPDATER_TGZ" ) \
            && echo "  ✓ $(basename "$UPDATER_TGZ")(.sig)" \
            || echo "  ⚠️  서명 실패"
        if [ -f "${UPDATER_TGZ}.sig" ]; then
            echo "  → latest.json darwin-${ARCH}.signature:"
            echo "    $(cat "${UPDATER_TGZ}.sig")"
        fi
    fi

    echo ""
    ls -lh "$DMG_OUT"
    shasum -a 256 "$DMG_OUT"
}

# ── 실행 ─────────────────────────────────────────────────────────────────────
mkdir -p "$BUILD_DIR"
echo "=== VEGA ${VERSION} DMG 빌드 (대상: ${BUILD_ARCH}) ==="

if [ "$BUILD_ARCH" = "all" ] || [ "$BUILD_ARCH" = "aarch64" ]; then
    build_arch aarch64
fi
if [ "$BUILD_ARCH" = "all" ] || [ "$BUILD_ARCH" = "x86_64" ]; then
    build_arch x86_64
fi

echo ""
echo "=== 완료 ==="
ls -lh "$BUILD_DIR"/*.dmg 2>/dev/null || true
echo ""
echo "설치:"
echo "  Apple Silicon: open \"$BUILD_DIR/${APP_NAME}-${VERSION}-aarch64.dmg\""
echo "  Intel Mac:     open \"$BUILD_DIR/${APP_NAME}-${VERSION}-x86_64.dmg\""
if [ -z "${VEGA_NOTARY_PROFILE:-}" ]; then
    echo ""
    echo "⚠️  공증 안 됨 — VEGA_NOTARY_PROFILE=vega-notary bash scripts/build_dmg.sh"
fi
