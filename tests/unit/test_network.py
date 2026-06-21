from fastapi.testclient import TestClient
import pytest
from web.server import app
from unittest.mock import patch, MagicMock

# Disable access gate for testing
app.middleware_stack = None
app.user_middleware = []
app.middleware_stack = app.build_middleware_stack()

class TestClientOverride(TestClient):
    def request(self, method, url, **kwargs):
        # Force localhost for testing
        if 'headers' not in kwargs or kwargs['headers'] is None:
            kwargs['headers'] = {}
        kwargs['headers']['host'] = 'localhost'
        return super().request(method, url, **kwargs)

client = TestClientOverride(app)


def test_tailscale_status_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="{\"Status\": \"Running\"}",
            stderr=""
        )
        response = client.get("/api/network/tailscale/status", headers={"host": "localhost"})
        assert response.status_code == 200
        assert response.json() == "{\"Status\": \"Running\"}"

def test_tailscale_status_not_installed():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError()
        response = client.get("/api/network/tailscale/status", headers={"host": "localhost"})
        assert response.status_code == 404
        assert "not installed" in response.json()["detail"]

def test_tailscale_status_timeout():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = TimeoutError()
        response = client.get("/api/network/tailscale/status", headers={"host": "localhost"})
        assert response.status_code == 504
        assert "timed out" in response.json()["detail"]

def test_wireguard_config_success():
    with patch("os.path.exists") as mock_exists, \
         patch("builtins.open", MagicMock()) as mock_open:
        mock_exists.return_value = True
        mock_open.return_value.__enter__.return_value.read.return_value = "abc123"
        
        response = client.get("/api/network/wireguard/config", headers={"host": "localhost"})
        assert response.status_code == 200
        assert "config" in response.json()
        assert "filename" in response.json()

def test_wireguard_config_no_keys():
    with patch("os.path.exists") as mock_exists:
        mock_exists.return_value = False
        response = client.get("/api/network/wireguard/config", headers={"host": "localhost"})
        assert response.status_code == 404
        assert "not generated" in response.json()["detail"]
