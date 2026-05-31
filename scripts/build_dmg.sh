#!/bin/bash
# VEGA macOS .dmg 빌드 스크립트 (daemon 풀에디션)
#
# 구조:
#   VEGA.app/Contents/MacOS/vega-backend  — PyInstaller 단일 바이너리
#   VEGA.app/Contents/Resources/          — settings HTML, LaunchAgent plist
#
# 첫 실행 시 Tauri가 LaunchAgent를 ~/Library/LaunchAgents/ 에 등록.
# 이후 로그인 시 백엔드가 자동 시작된다.
#
# 사전 요건:
#   - Rust + cargo-tauri
#   - mlx_env (pyinstaller 포함): source ~/dev/mlx_env/bin/activate
#   - hdiutil (macOS 기본 포함)
#   - Developer ID Application 코드서명 인증서

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="0.1.2"
APP_NAME="VEGA"
SIGN_APP="Developer ID Application: Heewon Oh (635QK74RYK)"
BUILD_DIR="$REPO_ROOT/build_output"
DMG_STAGE="$BUILD_DIR/dmg_stage"
DMG_OUT="$BUILD_DIR/${APP_NAME}-${VERSION}.dmg"

echo "=== VEGA ${VERSION} .dmg 빌드 시작 ==="

# ── 0. PyInstaller vega-backend 바이너리 빌드 ─────────────────────────────────
echo "[0/5] PyInstaller — vega-backend 바이너리 빌드..."

VEGA_VENV="$REPO_ROOT/bin/.venv"
if [ ! -x "$VEGA_VENV/bin/python3" ]; then
    echo "  격리 venv 생성 중..."
    python3 -m venv "$VEGA_VENV"
    "$VEGA_VENV/bin/pip" install --quiet pyinstaller
    "$VEGA_VENV/bin/pip" install --quiet -r "$REPO_ROOT/requirements.txt"
fi

"$VEGA_VENV/bin/pyinstaller" bin/vega-backend.spec \
    --distpath bin/dist \
    --workpath bin/build_pyinstaller \
    --noconfirm 2>&1 | grep -E "(ERROR|WARNING|INFO: Building EXE|INFO: Build complete)" || true

cp bin/dist/vega-backend bin/vega-backend
cp bin/dist/vega-backend "bin/vega-backend-aarch64-apple-darwin"
chmod +x bin/vega-backend "bin/vega-backend-aarch64-apple-darwin"
echo "  ✓ vega-backend ($(du -sh bin/vega-backend | cut -f1))"

# ── 1. Tauri 앱 빌드 ──────────────────────────────────────────────────────────
echo "[1/5] cargo tauri build..."
cd "$REPO_ROOT/desktop"
# 코드서명: Developer ID 인증서가 있으면 서명, 없으면 무서명(로컬/배포 테스트용)
if security find-identity -v -p codesigning 2>/dev/null | grep -q "$SIGN_APP"; then
    export APPLE_SIGNING_IDENTITY="$SIGN_APP"
    echo "  서명 인증서 발견 → 서명 빌드"
else
    echo "  (Developer ID 인증서 없음 → 무서명 빌드)"
fi
cargo tauri build --target aarch64-apple-darwin 2>&1 | grep -E "Compiling|Finished|error|Bundling"
TAURI_APP="$REPO_ROOT/desktop/target/aarch64-apple-darwin/release/bundle/macos/${APP_NAME}.app"
if [ ! -d "$TAURI_APP" ]; then
    echo "ERROR: VEGA.app 빌드 실패 — $TAURI_APP 없음" >&2
    exit 1
fi
cd "$REPO_ROOT"
echo "  ✓ VEGA.app"

# ── 2. DMG 스테이징 ───────────────────────────────────────────────────────────
echo "[2/5] DMG 스테이지 조립..."
mkdir -p "$BUILD_DIR"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"

cp -R "$TAURI_APP" "$DMG_STAGE/"
# /Applications 심볼릭 링크 (드래그&드롭 설치용)
ln -s /Applications "$DMG_STAGE/Applications"
echo "  ✓ 스테이지 완료 ($(du -sh "$DMG_STAGE/${APP_NAME}.app" | cut -f1))"

# ── 3. DMG 생성 ───────────────────────────────────────────────────────────────
echo "[3/5] hdiutil — DMG 생성..."
rm -f "$DMG_OUT"
hdiutil create \
    -volname "VEGA ${VERSION}" \
    -srcfolder "$DMG_STAGE" \
    -ov \
    -format UDZO \
    "$DMG_OUT"
echo "  ✓ DMG 생성"

# ── 4. DMG 서명 ───────────────────────────────────────────────────────────────
echo "[4/5] codesign — DMG 서명..."
if security find-certificate -c "Developer ID Application" &>/dev/null 2>&1; then
    codesign --sign "$SIGN_APP" --timestamp "$DMG_OUT"
    echo "  ✓ 서명 완료"
else
    echo "  (서명 인증서 없음 — 무서명)"
fi

# ── 5. 완료 ───────────────────────────────────────────────────────────────────
echo "[5/5] 완료"
ls -lh "$DMG_OUT"
echo ""
echo "설치:"
echo "  open \"$DMG_OUT\""
echo "  → VEGA.app을 Applications 폴더로 드래그"
echo "  → 첫 실행 시 백그라운드 데몬 자동 등록"
