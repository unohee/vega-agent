#!/bin/bash
# Install WireGuard on macOS
set -e

# Check if running on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "Error: This script is for macOS only."
    exit 1
fi

# Install WireGuard via Homebrew
echo "Installing WireGuard..."
if ! command -v wg &> /dev/null; then
    if ! command -v brew &> /dev/null; then
        echo "Homebrew not found. Installing..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    brew install wireguard-tools
fi

# Create config directory
echo "Creating WireGuard config directory..."
WG_DIR="$HOME/.config/wireguard"
mkdir -p "$WG_DIR"

# Generate key pair if not exists
if [[ ! -f "$WG_DIR/privatekey" ]]; then
    umask 077
    wg genkey | tee "$WG_DIR/privatekey" | wg pubkey > "$WG_DIR/publickey"
    echo "WireGuard keys generated."
fi

# Output client config template
cat > "$WG_DIR/client.conf" << 'EOF'
[Interface]
PrivateKey = $(cat $HOME/.config/wireguard/privatekey)
Address = 10.192.122.3/32
DNS = 1.1.1.1

[Peer]
PublicKey = <SERVER_PUBLIC_KEY>
AllowedIPs = 0.0.0.0/0
Endpoint = <SERVER_IP>:51820
PersistentKeepalive = 25
EOF

echo "\nWireGuard installed."
echo "Configure server with your public key: $(cat $HOME/.config/wireguard/publickey)"
echo "Edit $WG_DIR/client.conf with server details."
echo "Start with: sudo wg-quick up $WG_DIR/client.conf"
