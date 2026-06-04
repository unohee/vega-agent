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

VERSION="0.1.7"
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

# ── 자동 업데이트 서명 키 (updater 아티팩트의 .sig 생성에 필요) ────────────────
# tauri.conf.json 의 createUpdaterArtifacts: true 가 .app.tar.gz 와 .sig 를 만들려면
# 빌드 시점에 TAURI_SIGNING_PRIVATE_KEY 가 있어야 한다. 없으면 updater 아티팩트는
# 서명 없이 생성 시도되다 실패하므로, 키가 없을 땐 경고만 하고 계속 진행한다(DMG 자체는 생성됨).
UPDATER_KEY_PATH="${TAURI_SIGNING_PRIVATE_KEY_PATH:-$HOME/.tauri/vega-updater.key}"
if [ -f "$UPDATER_KEY_PATH" ]; then
    export TAURI_SIGNING_PRIVATE_KEY="$(cat "$UPDATER_KEY_PATH")"
    export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}"
    echo "  updater 서명 키: $UPDATER_KEY_PATH"
else
    echo "  ⚠️  updater 서명 키 없음($UPDATER_KEY_PATH) — updater 아티팩트(.sig) 생성 skip."
    echo "     키 생성: cargo tauri signer generate -w ~/.tauri/vega-updater.key --password \"\""
fi

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

# ── 4.5. 자동 업데이트 아티팩트 생성 ──────────────────────────────────────────
# updater 가 내려받아 적용할 패키지는 *재서명·공증된* VEGA.app 을 tar.gz 로 압축한 것.
# (cargo tauri build 가 자동 생성하는 .app.tar.gz 는 [1.5] 재서명 *이전*의 adhoc
#  앱이라 Gatekeeper 에 막힌다. 그래서 여기서 최종 앱으로 다시 만든다.)
# 서명 키가 있을 때만 수행. 산출물: build_output/VEGA-<ver>-aarch64.app.tar.gz(.sig)
UPDATER_DIR="$BUILD_DIR/updater"
if [ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
    echo "[4.5/5] updater 아티팩트(.app.tar.gz + .sig) 생성..."
    mkdir -p "$UPDATER_DIR"
    UPDATER_TGZ="$UPDATER_DIR/${APP_NAME}-${VERSION}-aarch64.app.tar.gz"
    # gzip tar 로 .app 통째 압축 (Tauri updater 가 기대하는 형식)
    tar -C "$(dirname "$TAURI_APP")" -czf "$UPDATER_TGZ" "$(basename "$TAURI_APP")"
    # minisign(.sig) 서명 — TAURI_SIGNING_PRIVATE_KEY(_PASSWORD) 환경변수 사용
    ( cd "$REPO_ROOT/desktop" && cargo tauri signer sign "$UPDATER_TGZ" ) \
        && echo "  ✓ $UPDATER_TGZ(.sig)" \
        || echo "  ⚠️  서명 실패 — .sig 미생성"
    # latest.json 채우기 도우미: .sig 내용을 출력
    if [ -f "${UPDATER_TGZ}.sig" ]; then
        echo "  → latest.json 의 darwin-aarch64.signature 에 넣을 값:"
        echo "    $(cat "${UPDATER_TGZ}.sig")"
    fi
else
    echo "[4.5/5] updater 아티팩트 skip (서명 키 없음)"
fi

# ── 5. 완료 ───────────────────────────────────────────────────────────────────
echo "[5/5] 완료"
ls -lh "$DMG_OUT"
shasum -a 256 "$DMG_OUT"
echo ""
echo "설치:"
echo "  open \"$DMG_OUT\""
echo "  → VEGA.app을 Applications 폴더로 드래그"
echo "  → 첫 실행 시 백그라운드 데몬 자동 등록"
if [ -d "$UPDATER_DIR" ]; then
    echo ""
    echo "자동 업데이트 배포(CF R2):"
    echo "  1) $UPDATER_DIR/*.app.tar.gz 를 R2 릴리스 경로에 업로드"
    echo "  2) desktop/updater/latest.json.template 을 채워(version/url/signature)"
    echo "     tauri.conf.json 의 endpoints 경로에 업로드"
    echo "  ⚠️  endpoints 는 현재 PLACEHOLDER — 실제 R2 도메인으로 교체 필요"
fi
if [ -z "${VEGA_NOTARY_PROFILE:-}" ]; then
    echo ""
    echo "⚠️  공증 안 됨 — 다른 맥에서 Gatekeeper 차단될 수 있음."
    echo "   공증하려면: VEGA_NOTARY_PROFILE=<프로필> bash scripts/build_dmg.sh"
fi
