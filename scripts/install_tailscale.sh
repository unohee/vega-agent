#!/bin/bash
# Install Tailscale on macOS
set -e

# Check if running on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "Error: This script is for macOS only."
    exit 1
fi

# Install Tailscale via Homebrew if not present
echo "Installing Tailscale..."
if ! command -v tailscale &> /dev/null; then
    if ! command -v brew &> /dev/null; then
        echo "Homebrew not found. Installing..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    brew install tailscale
fi

# Start and enable tailscaled
brew services start tailscale

# Print status
tailscale status

echo "\nTailscale installed and started."
echo "Run 'tailscale up' to authenticate this machine."
echo "Use MagicDNS to access: mac-mini.beta.tailscale.net:8100"
