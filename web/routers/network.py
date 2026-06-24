# Created: 2026-06-23
# Purpose: External access status/config API (Tailscale/WireGuard). Never returns private keys.

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/network", tags=["network"])


class WireGuardConfigIn(BaseModel):
    private_key: str | None = Field(default=None, alias="PrivateKey")
    address: str | None = Field(default=None, alias="Address")
    dns: str | None = Field(default=None, alias="DNS")
    peer_public_key: str | None = Field(default=None, alias="PublicKey")
    allowed_ips: str | None = Field(default=None, alias="AllowedIPs")
    endpoint: str | None = Field(default=None, alias="Endpoint")
    persistent_keepalive: int | None = Field(default=None, alias="PersistentKeepalive")

    class Config:
        populate_by_name = True


def _wg_dir() -> Path:
    return Path(os.environ.get("VEGA_WIREGUARD_DIR", Path.home() / ".config" / "wireguard"))


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _redacted_wg_config() -> dict:
    wg_dir = _wg_dir()
    public_key = _read_text(wg_dir / "publickey")
    conf_path = wg_dir / "client.conf"
    exists = conf_path.exists()
    return {
        "configured": exists,
        "config_path": str(conf_path),
        "public_key": public_key,
        # Explicitly advertise write-only semantics; do not include PrivateKey/private_key.
        "private_key": None,
        "private_key_redacted": True,
    }


@router.get("/status")
def network_status() -> dict:
    return {
        "tailscale": {
            "installed": any(Path(p, "tailscale").exists() for p in os.environ.get("PATH", "").split(os.pathsep)),
            "magic_dns": os.environ.get("VEGA_TAILSCALE_MAGIC_DNS"),
        },
        "wireguard": _redacted_wg_config(),
    }


@router.get("/wireguard")
def get_wireguard() -> dict:
    """Return WireGuard metadata without secret material."""
    return _redacted_wg_config()


@router.post("/wireguard")
def set_wireguard(config: WireGuardConfigIn) -> dict:
    """Accept WireGuard settings. PrivateKey is write-only and never echoed."""
    wg_dir = _wg_dir()
    wg_dir.mkdir(parents=True, exist_ok=True)

    if config.private_key:
        key_path = wg_dir / "privatekey"
        key_path.write_text(config.private_key.strip() + "\n", encoding="utf-8")
        try:
            key_path.chmod(0o600)
        except OSError:
            pass

    lines: list[str] = ["[Interface]"]
    if config.address:
        lines.append(f"Address = {config.address}")
    if config.dns:
        lines.append(f"DNS = {config.dns}")
    if config.private_key:
        lines.append(f"PrivateKey = {config.private_key.strip()}")
    lines.append("")
    lines.append("[Peer]")
    if config.peer_public_key:
        lines.append(f"PublicKey = {config.peer_public_key}")
    if config.allowed_ips:
        lines.append(f"AllowedIPs = {config.allowed_ips}")
    if config.endpoint:
        lines.append(f"Endpoint = {config.endpoint}")
    if config.persistent_keepalive is not None:
        lines.append(f"PersistentKeepalive = {config.persistent_keepalive}")

    (wg_dir / "client.conf").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return _redacted_wg_config()
