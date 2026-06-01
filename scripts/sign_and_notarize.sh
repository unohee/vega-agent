#!/bin/bash
# Created: 2026-06-02
# Purpose: VEGA.app 을 Developer ID + entitlements 로 깊이 재서명하고(필수),
#   notarytool 프로필이 있으면 공증·staple 까지 자동 수행한다.
#
#   왜 필요한가 (FIX_0601.md):
#     - `cargo tauri build` 만으로는 앱 내부 externalBin(vega-backend)에
#       entitlements(disable-library-validation)가 안 박혀 hardened runtime 이
#       PyInstaller Python.framework(다른 Team ID) dlopen 을 차단한다(PYI-30816).
#     - 그래서 내부 바이너리부터 안→밖 순서로 --entitlements 명시 재서명이 필수.
#     - 이 단계가 빌드 스크립트에 없어 매번 수동으로 하다 entitlement 를 빠뜨렸다.
#
# 사용:
#   scripts/sign_and_notarize.sh <APP_PATH> [DMG_OR_PKG_PATH]
#     → 앱 deep 재서명 후, 산출물(있으면) 서명 + (프로필 있으면) 공증.
#   scripts/sign_and_notarize.sh --artifact-only <DMG_OR_PKG_PATH>
#     → 앱 재서명 없이 산출물만 서명 + (프로필 있으면) 공증·staple.
#       (앱이 이미 별도로 서명된 경우 DMG/PKG 단계에서 사용)
#
# 환경변수(선택):
#   VEGA_SIGN_ID         서명 ID (기본: "Developer ID Application: Heewon Oh (635QK74RYK)")
#   VEGA_ENTITLEMENTS    entitlements.plist 경로 (기본: desktop/entitlements.plist)
#   VEGA_NOTARY_PROFILE  notarytool keychain-profile 이름 (있으면 공증 시도)
#
# 반환: 서명 실패 시 비0 종료. 공증 프로필 없으면 공증은 skip(0 종료, 안내 출력).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIGN_ID="${VEGA_SIGN_ID:-Developer ID Application: Heewon Oh (635QK74RYK)}"
ENT="${VEGA_ENTITLEMENTS:-$REPO_ROOT/desktop/entitlements.plist}"
NOTARY_PROFILE="${VEGA_NOTARY_PROFILE:-}"
# 서명에 쓸 키체인. private key 가 있는 키체인을 unlock + partition list 설정해야
# codesign 이 errSecInternalComponent 없이 private key 에 접근한다.
#   VEGA_KEYCHAIN     키체인 경로 (없으면 login.keychain-db 사용)
#   VEGA_KEYCHAIN_PW  그 키체인 잠금 해제 비번 (login 키체인이면 보통 불필요)
SIGN_KEYCHAIN="${VEGA_KEYCHAIN:-}"
SIGN_KEYCHAIN_PW="${VEGA_KEYCHAIN_PW:-}"

# --artifact-only 모드: 앱 재서명 없이 DMG/PKG 만 서명·공증.
ARTIFACT_ONLY=0
if [ "${1:-}" = "--artifact-only" ]; then
    ARTIFACT_ONLY=1
    shift
    ARTIFACT="${1:?사용법: sign_and_notarize.sh --artifact-only <DMG_OR_PKG_PATH>}"
    APP_PATH=""
else
    APP_PATH="${1:?사용법: sign_and_notarize.sh <APP_PATH> [DMG_OR_PKG_PATH]}"
    ARTIFACT="${2:-}"
fi

if [ ! -f "$ENT" ]; then
    echo "ERROR: entitlements 없음: $ENT" >&2
    exit 1
fi
if [ "$ARTIFACT_ONLY" -eq 0 ] && [ ! -d "$APP_PATH" ]; then
    echo "ERROR: 앱 번들 없음: $APP_PATH" >&2
    exit 1
fi

# 서명 인증서가 이 머신에 있는지 확인 — 없으면 서명 단계 자체를 건너뛴다(로컬 테스트).
if ! security find-identity -v -p codesigning 2>/dev/null | grep -q "$SIGN_ID"; then
    echo "  (서명 인증서 '$SIGN_ID' 없음 → 재서명/공증 skip, adhoc 빌드 그대로)"
    exit 0
fi

# 키체인 준비: unlock + partition list 설정.
# 이게 없으면 codesign 이 private key 접근에서 errSecInternalComponent 로 실패한다.
# --keychain 인자로 넘길 값(없으면 빈 문자열 → codesign 이 search list 사용).
KC_ARGS=()
if [ -n "$SIGN_KEYCHAIN" ]; then
    KC_ARGS=(--keychain "$SIGN_KEYCHAIN")
    if [ -n "$SIGN_KEYCHAIN_PW" ]; then
        security unlock-keychain -p "$SIGN_KEYCHAIN_PW" "$SIGN_KEYCHAIN" 2>/dev/null || true
        security set-key-partition-list -S apple-tool:,apple:,codesign: \
            -s -k "$SIGN_KEYCHAIN_PW" "$SIGN_KEYCHAIN" >/dev/null 2>&1 || true
    fi
    echo "  서명 키체인: $SIGN_KEYCHAIN"
fi

sign_one() {
    local target="$1"
    codesign --force --options runtime --timestamp \
        "${KC_ARGS[@]}" \
        --entitlements "$ENT" \
        --sign "$SIGN_ID" \
        "$target"
}

if [ "$ARTIFACT_ONLY" -eq 0 ]; then
    echo "=== 코드 서명 (Developer ID + entitlements) ==="
    echo "  ID:  $SIGN_ID"
    echo "  ENT: $ENT"

    # 1. 내부 실행 파일부터 안→밖 순서로 재서명 (codesign 규칙: 내부 먼저).
    #    Contents/MacOS/* + Resources 안의 모든 Mach-O 바이너리.

    # Contents/MacOS 의 모든 실행 파일 (vega-backend, vega-desktop 등)
    if [ -d "$APP_PATH/Contents/MacOS" ]; then
        for bin in "$APP_PATH/Contents/MacOS"/*; do
            [ -f "$bin" ] || continue
            echo "  서명: Contents/MacOS/$(basename "$bin")"
            sign_one "$bin"
        done
    fi

    # Resources/Frameworks 안에 dylib/framework 가 있으면 같이 서명 (있을 때만)
    while IFS= read -r macho; do
        [ -n "$macho" ] || continue
        echo "  서명: ${macho#$APP_PATH/}"
        sign_one "$macho"
    done < <(find "$APP_PATH/Contents" \( -name "*.dylib" -o -name "*.so" \) 2>/dev/null)

    # 2. 앱 번들 전체를 마지막에 deep 서명.
    echo "  서명: $(basename "$APP_PATH") (deep)"
    codesign --force --deep --options runtime --timestamp \
        "${KC_ARGS[@]}" \
        --entitlements "$ENT" \
        --sign "$SIGN_ID" \
        "$APP_PATH"

    # 3. 서명 검증.
    echo "=== 서명 검증 ==="
    codesign --verify --deep --strict --verbose=2 "$APP_PATH"
    echo "  --- entitlement 포함 확인 ---"
    for bin in "$APP_PATH/Contents/MacOS"/*; do
        [ -f "$bin" ] || continue
        if codesign -d --entitlements - "$bin" 2>/dev/null | grep -q "disable-library-validation"; then
            echo "  ✓ $(basename "$bin"): disable-library-validation 포함"
        else
            echo "  ✗ $(basename "$bin"): disable-library-validation 누락!" >&2
            exit 1
        fi
    done
fi

# 4. 산출물(DMG/PKG)이 주어졌으면 그것도 서명.
if [ -n "$ARTIFACT" ] && [ -f "$ARTIFACT" ]; then
    echo "=== 산출물 서명: $(basename "$ARTIFACT") ==="
    codesign --force --timestamp "${KC_ARGS[@]}" --sign "$SIGN_ID" "$ARTIFACT"
    codesign --verify --verbose=2 "$ARTIFACT"
fi

# 5. 공증 (notarytool 프로필이 있을 때만).
if [ -z "$NOTARY_PROFILE" ]; then
    echo ""
    echo "=== 공증 skip ==="
    echo "  VEGA_NOTARY_PROFILE 미설정 → 공증/staple 생략."
    echo "  공증하려면: xcrun notarytool store-credentials <프로필명> 로 자격증명 저장 후"
    echo "  VEGA_NOTARY_PROFILE=<프로필명> 로 재실행."
    exit 0
fi

NOTARIZE_TARGET="${ARTIFACT:-$APP_PATH}"
echo ""
echo "=== 공증 (notarytool, 프로필: $NOTARY_PROFILE) ==="
echo "  대상: $NOTARIZE_TARGET"
xcrun notarytool submit "$NOTARIZE_TARGET" \
    --keychain-profile "$NOTARY_PROFILE" \
    --wait

echo "=== staple ==="
xcrun stapler staple "$NOTARIZE_TARGET"
xcrun stapler validate "$NOTARIZE_TARGET"

echo "=== Gatekeeper 평가 ==="
if [[ "$NOTARIZE_TARGET" == *.dmg ]]; then
    spctl -a -vvv -t install "$NOTARIZE_TARGET" || true
else
    spctl -a -vvv -t exec "$NOTARIZE_TARGET" || true
fi

echo "✓ 서명 + 공증 + staple 완료"
