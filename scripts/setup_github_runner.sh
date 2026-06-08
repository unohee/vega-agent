#!/bin/bash
# GitHub Actions self-hosted runner 설치 스크립트
# 대상: Intrect-io/vega-agent (macOS arm64)
#
# 사용:
#   bash scripts/setup_github_runner.sh <REGISTRATION_TOKEN>
#
# 토큰 발급:
#   gh api -X POST repos/Intrect-io/vega-agent/actions/runners/registration-token

set -euo pipefail

TOKEN="${1:?사용법: $0 <REGISTRATION_TOKEN>}"
RUNNER_DIR="$HOME/dev/gh-runner-vega"
RUNNER_VERSION="2.334.0"
REPO_URL="https://github.com/Intrect-io/vega-agent"
RUNNER_NAME="${RUNNER_NAME:-vega-mac-$(hostname -s)}"
RUNNER_LABELS="self-hosted,macOS,ARM64,vega"

echo "=== GitHub Actions Runner 설치 ==="
echo "  저장소: $REPO_URL"
echo "  이름:   $RUNNER_NAME"
echo "  경로:   $RUNNER_DIR"

# 1. 다운로드
mkdir -p "$RUNNER_DIR"
if [ ! -f "$RUNNER_DIR/run.sh" ]; then
    echo "[1/4] runner 다운로드 (v${RUNNER_VERSION})..."
    curl -L "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-osx-arm64-${RUNNER_VERSION}.tar.gz" \
        -o "/tmp/actions-runner.tar.gz"
    tar -xzf /tmp/actions-runner.tar.gz -C "$RUNNER_DIR"
    echo "  ✓ 압축 해제 완료"
else
    echo "[1/4] runner 바이너리 이미 있음 — skip"
fi

# 2. 등록
echo "[2/4] runner 등록..."
cd "$RUNNER_DIR"
./config.sh \
    --url "$REPO_URL" \
    --token "$TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "$RUNNER_LABELS" \
    --work "_work" \
    --unattended \
    --replace
echo "  ✓ 등록 완료"

# 3. LaunchAgent 설치 (로그인 시 자동 시작)
echo "[3/4] LaunchAgent 설치..."
PLIST_LABEL="com.unohee.gh-runner-vega"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${RUNNER_DIR}/run.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${RUNNER_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/gh-runner-vega.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/gh-runner-vega.stderr.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/${PLIST_LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
echo "  ✓ LaunchAgent 등록 및 기동"

# 4. 상태 확인
echo "[4/4] 상태 확인..."
sleep 3
if launchctl list | grep -q "$PLIST_LABEL"; then
    echo "  ✓ runner 실행 중"
    echo "  로그: tail -f /tmp/gh-runner-vega.stdout.log"
else
    echo "  ⚠️  runner 미실행 — 로그 확인: /tmp/gh-runner-vega.stderr.log"
fi

echo ""
echo "=== 완료 ==="
echo "GitHub → Settings → Actions → Runners 에서 연결 확인"
echo ""
echo "Secrets 설정 필요 (repo settings → Secrets):"
echo "  APPLE_ID                      — Apple 계정 이메일 (공증용)"
echo "  APPLE_TEAM_ID                 — 635QK74RYK"
echo "  APPLE_APP_SPECIFIC_PASSWORD   — App-Specific Password"
echo "  TAURI_SIGNING_PRIVATE_KEY     — cat ~/.tauri/vega-updater.key"
echo "  TAURI_SIGNING_PRIVATE_KEY_PASSWORD — (비어있으면 생략)"
