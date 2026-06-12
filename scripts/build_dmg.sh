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

# 버전의 단일 출처 = tauri.conf.json. CI(release-dmg.yml)가 이 줄을 sed 로
# tauri.conf 값과 맞춰 주입한다(^VERSION="..." 패턴 의존). 로컬 수동 빌드 시엔
# 이 값을 tauri.conf.json 과 손으로 맞춰야 한다 (INT-1432).
VERSION="0.1.20"
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

# ── 배포 기본 키 번들 생성 ────────────────────────────────────────────────────
# repo .env(gitignore)에서 검색 게이트웨이 키(VEGA_API_KEY)만 추출해
# bin/bundle_env/.env 로 스테이징 → spec 이 번들 루트(_MEIPASS/.env)에 싣는다.
# keychain.get 의 .env 폴백 체인이 frozen 앱에서 이 파일을 찾는다 (keychain.py 참조).
# 키 없이 빌드하면 배포 사용자 web_search 가 401 — 조용히 빠뜨리지 않고 실패시킨다.
echo "[pre] 배포 기본 키 번들 생성..."
# 키 출처 우선순위: 환경변수(CI=GitHub Secret 주입) > repo .env(로컬 개발).
# CI 러너엔 .env(gitignore)가 없으므로 환경변수 경로가 정상. set -e 하에서
# grep 실패가 스크립트를 죽이지 않도록 .env 존재 시에만 grep 하고 `|| true` 로 감싼다.
# 표준 이름 VEGA_SEARXNG_KEY 우선, 구명칭 VEGA_API_KEY 폴백 (tools_web 해석 순서와 동일).
VEGA_BUNDLE_KEY="${VEGA_SEARXNG_KEY:-${VEGA_API_KEY:-}}"
if [ -z "$VEGA_BUNDLE_KEY" ] && [ -f "$REPO_ROOT/.env" ]; then
    VEGA_BUNDLE_KEY="$(grep -E '^VEGA_SEARXNG_KEY=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
    [ -n "$VEGA_BUNDLE_KEY" ] || VEGA_BUNDLE_KEY="$(grep -E '^VEGA_API_KEY=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
fi
if [ -z "$VEGA_BUNDLE_KEY" ]; then
    echo "  ERROR: $REPO_ROOT/.env 에 VEGA_SEARXNG_KEY(또는 VEGA_API_KEY) 없음 — 배포본 web_search 가 깨진다." >&2
    echo "         키를 .env 에 추가하거나, 의도적으로 뺄 거면 VEGA_SKIP_BUNDLE_KEY=1 로 재실행." >&2
    [ "${VEGA_SKIP_BUNDLE_KEY:-0}" = "1" ] || exit 1
else
    mkdir -p "$REPO_ROOT/bin/bundle_env"
    printf '# 배포 기본값 — 빌드 시 자동 생성 (scripts/build_dmg.sh). 커밋 금지.\nVEGA_SEARXNG_KEY=%s\n' "$VEGA_BUNDLE_KEY" > "$REPO_ROOT/bin/bundle_env/.env"
    echo "  ✓ bin/bundle_env/.env (VEGA_SEARXNG_KEY)"
fi

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

    # 1.5. 재서명 + (프로필 있으면) .app 공증·staple
    # .app 자체를 staple 해야 OTA updater tar.gz 안의 앱이 ticket 을 갖는다.
    # (DMG 만 공증하면 직접설치는 되지만 tar.gz 안 .app 은 staple 누락 → 오프라인/
    #  자동설치 Gatekeeper 리스크). VEGA_NOTARY_PROFILE 을 그대로 전파.
    echo "[1.5] 재서명 (Developer ID + entitlements)$([ -n "${VEGA_NOTARY_PROFILE:-}" ] && echo ' + .app 공증·staple')..."
    VEGA_SIGN_ID="$SIGN_APP" \
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
