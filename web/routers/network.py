from fastapi import APIRouter, HTTPException
import subprocess
import os

router = APIRouter(prefix="/api/network", tags=["network"])

@router.get("/tailscale/status")
async def get_tailscale_status():
    """Get Tailscale daemon status"""
    try:
        result = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Tailscale error: {result.stderr}")
        return result.stdout
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Tailscale not installed")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Tailscale command timed out")

@router.get("/wireguard/config")
async def get_wireguard_config():
    """Generate WireGuard client config"""
    wg_dir = os.path.expanduser("~/.config/wireguard")
    private_key_path = os.path.join(wg_dir, "privatekey")
    
    if not os.path.exists(private_key_path):
        raise HTTPException(status_code=404, detail="WireGuard keys not generated")
    
    try:
        with open(private_key_path, 'r') as f:
            private_key = f.read().strip()
            
        # This would normally come from server config
        # For now, use placeholder values
        config = f"""[Interface]
PrivateKey = {private_key}
Address = 10.19.23.42/32
DNS = 10.19.23.1

[Peer]
PublicKey = <SERVER_PUBLIC_KEY>
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = <SERVER_IP>:51820
PersistentKeepalive = 25
"""
        
        return {"config": config, "filename": "vega-client.conf"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate config: {str(e)}")