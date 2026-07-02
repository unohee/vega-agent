# Created: 2026-07-02
# Purpose: Manage the GoWA (go-whatsapp-web-multidevice) REST server as an opt-in
#   VEGA-managed sidecar process. Started/stopped from web/server.py lifespan.
#   The whatsapp_* tools (pipeline/tools_whatsapp.py) talk to this server over REST.
# Dependencies: stdlib only (subprocess, urllib), pipeline.data_paths
# Test Status: tests/test_whatsapp_sidecar.py

from __future__ import annotations

import os
import subprocess
import urllib.request
from pathlib import Path

# Opt-in: sidecar only starts when this env flag is truthy. Off by default so the
# backend never spawns an unofficial-protocol WhatsApp server unless the user asks.
_OPT_IN_ENV = "VEGA_WHATSAPP_SIDECAR"
# Port must match tools_whatsapp default (VEGA_WHATSAPP_GOWA_URL) — keep in sync.
_DEFAULT_PORT = 3777

_proc: subprocess.Popen | None = None


def is_enabled() -> bool:
    """True if the GoWA sidecar is opted in via env (1/true/yes/on)."""
    return os.getenv(_OPT_IN_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _binary_path() -> Path:
    """GoWA binary location. Override with VEGA_WHATSAPP_GOWA_BIN; else data_dir default."""
    override = os.getenv("VEGA_WHATSAPP_GOWA_BIN")
    if override:
        return Path(override).expanduser()
    try:
        from pipeline.data_paths import data_dir
        base = Path(data_dir())
    except Exception:
        base = Path(os.getenv("VEGA_DATA_DIR", ".vega"))
    return base / "whatsapp-gowa" / "whatsapp"


def _port() -> int:
    try:
        return int(os.getenv("VEGA_WHATSAPP_GOWA_PORT", str(_DEFAULT_PORT)))
    except ValueError:
        return _DEFAULT_PORT


def _already_serving(port: int, timeout: float = 1.5) -> bool:
    """True if something already answers on the GoWA port (manual start / prior run)."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/", timeout=timeout):
            return True
    except Exception:
        return False


def start() -> dict:
    """Start the GoWA sidecar if opted in and not already running.

    Returns a status dict — never raises (lifespan must not fail on sidecar issues).
    """
    global _proc
    if not is_enabled():
        return {"started": False, "reason": "not opted in"}
    port = _port()
    if _already_serving(port):
        return {"started": False, "reason": f"already serving on {port}"}
    binary = _binary_path()
    if not binary.is_file():
        return {"started": False, "reason": f"binary not found: {binary}"}
    if not os.access(binary, os.X_OK):
        return {"started": False, "reason": f"binary not executable: {binary}"}
    try:
        # rest mode on the shared port; own process group so we can clean it up.
        _proc = subprocess.Popen(
            [str(binary), "rest", "--port", str(port)],
            cwd=str(binary.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"started": True, "pid": _proc.pid, "port": port}
    except Exception as e:
        _proc = None
        return {"started": False, "reason": f"spawn failed: {e}"}


def stop() -> None:
    """Terminate the sidecar if VEGA started it (no-op for manually-run servers)."""
    global _proc
    if _proc is None:
        return
    try:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
    except Exception:
        pass
    finally:
        _proc = None
