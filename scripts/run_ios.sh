#!/usr/bin/env bash
# VEGA iOS 프로토타입 — 시뮬레이터 실행 헬퍼
#
# 선행 조건 (1회만, sudo 필요):
#   sudo xcode-select -s /Applications/Xcode.app   # 이미 설정됨
#   sudo xcodebuild -runFirstLaunch                # CoreSimulator 설치
#   xcodebuild -downloadPlatform iOS               # iOS 시뮬레이터 런타임
#
# 사용:
#   bash scripts/run_ios.sh                         # 기본: localhost:8100 (시뮬레이터→Mac 백엔드)
#   VEGA_SERVER_URL=https://my.server bash scripts/run_ios.sh   # 원격 백엔드 지정
#
# 시뮬레이터는 호스트 Mac의 localhost를 공유하므로, Mac에서 vega-backend가
# 8100에 떠 있으면 별도 설정 없이 http://localhost:8100 으로 붙는다.
set -euo pipefail

cd "$(dirname "$0")/.."/desktop

# rustup shim(iOS std 타깃 인식) + brew 도구(pod 등)를 PATH 앞에 둔다.
export PATH="/opt/homebrew/opt/rustup/bin:/opt/homebrew/bin:$PATH"

# CoreSimulator 미설치 시 친절한 안내 후 종료.
if ! xcrun simctl list runtimes >/dev/null 2>&1; then
  echo "❌ iOS 시뮬레이터 런타임이 없습니다. 먼저 아래를 실행하세요(sudo 필요):"
  echo "   sudo xcodebuild -runFirstLaunch"
  echo "   xcodebuild -downloadPlatform iOS"
  exit 1
fi

echo "▶ tauri ios dev — 백엔드: ${VEGA_SERVER_URL:-http://localhost:8100}"
# default feature(daemon)로 빌드해도 daemon 코드는 not(mobile)로 자동 배제된다.
exec cargo tauri ios dev
