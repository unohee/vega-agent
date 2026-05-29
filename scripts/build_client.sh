#!/bin/bash
# VEGA Client macOS .dmg 빌드 스크립트 (CE — Community Edition)
#
# CE 앱은 백엔드 sidecar 없이 외부 VEGA 서버에 연결하는 얇은 Tauri 셸.
# 로컬 시스템 도구(host_exec, file_read 등)는 서버에서 자동 차단됨.
#
# 사전 요건:
#   - Rust + cargo-tauri: cargo install tauri-cli
#   - Developer ID Application 코드서명 인증서 (없으면 무서명 .dmg 생성)
#
# 실행:
#   cd /path/to/VEGA && bash scripts/build_client.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT/desktop"

VERSION="0.1.0"
APP_NAME="VEGA Client"
CONF="tauri.client.conf.json"

echo "=== ${APP_NAME} ${VERSION} .dmg 빌드 시작 ==="
echo "    config : desktop/${CONF}"
echo "    feature: client (sidecar 없음)"
echo ""

# ── Tauri 빌드 (client feature) ───────────────────────────────────────────────
cargo tauri build \
    --features client \
    --config "$CONF" \
    --target aarch64-apple-darwin \
    2>&1 | grep -E "Compiling|Finished|error\[|Bundling|tauri"

# ── 결과물 위치 확인 ───────────────────────────────────────────────────────────
BUNDLE_DIR="$REPO_ROOT/desktop/target/aarch64-apple-darwin/release/bundle"
DMG_PATH=$(find "$BUNDLE_DIR/dmg" -name "*.dmg" 2>/dev/null | head -1)

if [ -z "$DMG_PATH" ]; then
    echo ""
    echo "ERROR: .dmg 파일을 찾을 수 없습니다 — 빌드 로그를 확인해주세요." >&2
    exit 1
fi

echo ""
echo "=== 빌드 완료 ==="
ls -lh "$DMG_PATH"
echo ""
echo "배포:"
echo "  열기: open \"$DMG_PATH\""
echo "  VEGA Client.app 을 /Applications 로 드래그하면 설치 완료."
echo ""
echo "첫 실행 후 트레이 → 설정 에서 서버 URL을 입력하세요."
