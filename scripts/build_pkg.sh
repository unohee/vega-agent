#!/bin/bash
# VEGA macOS .pkg 인스톨러 빌드 스크립트
#
# 사전 요건:
#   - Xcode Command Line Tools (pkgbuild, productbuild)
#   - Rust + cargo-tauri (cargo install tauri-cli)
#   - mlx_env (pyinstaller 포함): source ~/dev/mlx_env/bin/activate
#   - Developer ID Application / Installer 코드서명 인증서
#
# 실행:
#   cd /path/to/VEGA && bash scripts/build_pkg.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="0.1.2"
APP_NAME="VEGA"
IDENTIFIER="com.unohee.vega"
SIGN_APP="Developer ID Application: Heewon Oh (635QK74RYK)"
SIGN_PKG="Developer ID Installer: Heewon Oh (635QK74RYK)"
BUILD_DIR="$REPO_ROOT/build_output"
SRC_ROOT="$BUILD_DIR/src-root"

echo "=== VEGA ${VERSION} .pkg 빌드 시작 ==="

# ── 0. PyInstaller로 vega-backend 바이너리 빌드 ───────────────────────────────
echo "[0/6] PyInstaller — vega-backend 바이너리 빌드..."

# VEGA 전용 격리 venv 사용 — mlx_env 전체 패키지 스캔 방지 (속도 10x 향상)
VEGA_VENV="$REPO_ROOT/bin/.venv"
if [ ! -x "$VEGA_VENV/bin/python3" ]; then
    echo "  격리 venv 생성 중..."
    python3 -m venv "$VEGA_VENV"
    "$VEGA_VENV/bin/pip" install --quiet pyinstaller
    "$VEGA_VENV/bin/pip" install --quiet -r "$REPO_ROOT/requirements.txt"
fi
PYINSTALLER="$VEGA_VENV/bin/pyinstaller"

"$PYINSTALLER" bin/vega-backend.spec \
    --distpath bin/dist \
    --workpath bin/build_pyinstaller \
    --noconfirm 2>&1 | grep -E "(ERROR|WARNING|INFO: Building EXE|INFO: Build complete)" || true

# Tauri externalBin이 참조하는 플랫폼 접미사 파일로 복사
cp bin/dist/vega-backend bin/vega-backend
cp bin/dist/vega-backend bin/vega-backend-aarch64-apple-darwin
chmod +x bin/vega-backend bin/vega-backend-aarch64-apple-darwin
echo "  ✓ vega-backend ($(du -sh bin/vega-backend | cut -f1))"

# ── 1. Tauri 앱 빌드 ──────────────────────────────────────────────────────────
echo "[1/6] cargo tauri build..."
cd "$REPO_ROOT/desktop"
cargo tauri build --target aarch64-apple-darwin 2>&1 | grep -E "Compiling|Finished|error|Bundling"
TAURI_APP="$REPO_ROOT/desktop/target/aarch64-apple-darwin/release/bundle/macos/${APP_NAME}.app"
if [ ! -d "$TAURI_APP" ]; then
    echo "ERROR: VEGA.app 빌드 실패 — $TAURI_APP 없음" >&2
    exit 1
fi
cd "$REPO_ROOT"
echo "  ✓ VEGA.app"

# ── 2. src-root 조립 ──────────────────────────────────────────────────────────
echo "[2/6] 설치 이미지 조립..."
mkdir -p "$BUILD_DIR"
rm -rf "$SRC_ROOT"
mkdir -p "$SRC_ROOT/Applications"

# VEGA.app 복사
cp -R "$TAURI_APP" "$SRC_ROOT/Applications/"

# 백엔드 소스를 VEGA.app/Contents/Resources/vega_backend/ 에 복사
BACKEND_DEST="$SRC_ROOT/Applications/${APP_NAME}.app/Contents/Resources/vega_backend"
mkdir -p "$BACKEND_DEST"

rsync -a \
    --exclude="__pycache__" --exclude="*.pyc" --exclude=".git" \
    --exclude="build_output" --exclude="testing" --exclude="sandbox" \
    --exclude="node_modules" --exclude="desktop/target" --exclude="desktop/" \
    --exclude="bin/" --exclude="logs/" --exclude="trash/" \
    --exclude=".claude/" --exclude=".deepeval/" --exclude=".ruff_cache/" \
    --include="app.py" \
    --include="requirements.txt" \
    --include="pipeline/" --include="pipeline/**" \
    --include="web/" --include="web/**" \
    --include="data/" \
    --include="data/commands/" --include="data/commands/**" \
    --include="data/agents/" --include="data/agents/**" \
    --include="data/mcp.json" \
    --include="public/" --include="public/**" \
    --include="scripts/" \
    --include="scripts/init_user_db.py" \
    --exclude="data/*" \
    --exclude="scripts/*" \
    --exclude="*" \
    "$REPO_ROOT/" \
    "$BACKEND_DEST/"

echo "  ✓ 백엔드 소스 복사 완료"

# ── 3. 컴포넌트 패키지 빌드 ──────────────────────────────────────────────────
echo "[3/6] pkgbuild..."
pkgbuild \
    --root "$SRC_ROOT" \
    --identifier "$IDENTIFIER" \
    --version "$VERSION" \
    --scripts "$REPO_ROOT/build/scripts" \
    --install-location "/" \
    "$BUILD_DIR/${APP_NAME}.pkg"
echo "  ✓ ${APP_NAME}.pkg"

# ── 4. 배포 패키지 빌드 (서명 있으면 서명, 없으면 무서명) ─────────────────────
echo "[4/6] productbuild..."
if security find-certificate -c "Developer ID Installer" &>/dev/null 2>&1; then
    productbuild \
        --distribution "$REPO_ROOT/build/Distribution.xml" \
        --package-path "$BUILD_DIR" \
        --sign "$SIGN_PKG" \
        "$BUILD_DIR/${APP_NAME}-${VERSION}.pkg"
else
    echo "  (서명 인증서 없음 — 무서명 패키지)"
    productbuild \
        --distribution "$REPO_ROOT/build/Distribution.xml" \
        --package-path "$BUILD_DIR" \
        "$BUILD_DIR/${APP_NAME}-${VERSION}.pkg"
fi

# ── 5. 결과 확인 ──────────────────────────────────────────────────────────────
PKG_PATH="$BUILD_DIR/${APP_NAME}-${VERSION}.pkg"
echo "[5/6] 완료"
ls -lh "$PKG_PATH"
echo ""
echo "설치:"
echo "  sudo installer -pkg \"$PKG_PATH\" -target /"
echo "  또는 더블클릭"
