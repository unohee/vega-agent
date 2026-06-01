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

VERSION="0.1.3"
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
# Tauri 의 자동 codesign 은 끈다(APPLE_SIGNING_IDENTITY 미설정 → adhoc 빌드).
# 이유: Tauri 자동 서명은 키체인 partition list 미설정 시 errSecInternalComponent
# 로 빌드 자체를 죽인다. 서명은 [1.5] 에서 sign_and_notarize.sh 가 키체인을
# unlock/partition 설정한 뒤 entitlements 와 함께 전담한다.
unset APPLE_SIGNING_IDENTITY || true
# --bundles app: app 번들만 생성. dmg 타겟은 create-dmg(osascript/Finder)로 헤드리스
# 환경에서 hang 하므로 제외 — DMG 는 아래 hdiutil 단계에서 만든다.
cargo tauri build --target aarch64-apple-darwin --bundles app 2>&1 | grep -E "Compiling|Finished|error|Bundling"
TAURI_APP="$REPO_ROOT/desktop/target/aarch64-apple-darwin/release/bundle/macos/${APP_NAME}.app"
if [ ! -d "$TAURI_APP" ]; then
    echo "ERROR: VEGA.app 빌드 실패 — $TAURI_APP 없음" >&2
    exit 1
fi
cd "$REPO_ROOT"
echo "  ✓ VEGA.app (adhoc — [1.5]에서 Developer ID 재서명)"

# ── 1.5. 앱 재서명 (entitlements 강제) ────────────────────────────────────────
# cargo tauri build 만으로는 내부 vega-backend 에 entitlements 가 안 박혀
# hardened runtime 이 PyInstaller Python.framework 를 차단한다(PYI-30816).
# DMG 에 담기 *전에* 내부 바이너리부터 deep 재서명한다.
# VEGA_KEYCHAIN/VEGA_KEYCHAIN_PW 가 설정돼 있으면 그 키체인을 unlock/partition 설정.
echo "[1.5/5] 앱 재서명 (Developer ID + entitlements)..."
# 공증은 여기서 하지 않는다(notarytool 은 .app 직접 제출 불가 — DMG 로 감싼 뒤 [4]에서).
# VEGA_NOTARY_PROFILE 을 명시적으로 비워 앱 단계 공증을 막는다.
VEGA_SIGN_ID="$SIGN_APP" VEGA_NOTARY_PROFILE="" bash "$REPO_ROOT/scripts/sign_and_notarize.sh" "$TAURI_APP"

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

# ── 4. DMG 서명 + (조건부) 공증/staple ────────────────────────────────────────
# VEGA_NOTARY_PROFILE 가 설정돼 있으면 DMG 를 공증·staple 까지 자동 수행.
# 없으면 서명만 하고 공증은 안내 후 skip.
echo "[4/5] DMG 서명/공증..."
VEGA_SIGN_ID="$SIGN_APP" bash "$REPO_ROOT/scripts/sign_and_notarize.sh" --artifact-only "$DMG_OUT"

# ── 5. 완료 ───────────────────────────────────────────────────────────────────
echo "[5/5] 완료"
ls -lh "$DMG_OUT"
shasum -a 256 "$DMG_OUT"
echo ""
echo "설치:"
echo "  open \"$DMG_OUT\""
echo "  → VEGA.app을 Applications 폴더로 드래그"
echo "  → 첫 실행 시 백그라운드 데몬 자동 등록"
if [ -z "${VEGA_NOTARY_PROFILE:-}" ]; then
    echo ""
    echo "⚠️  공증 안 됨 — 다른 맥에서 Gatekeeper 차단될 수 있음."
    echo "   공증하려면: VEGA_NOTARY_PROFILE=<프로필> bash scripts/build_dmg.sh"
fi
