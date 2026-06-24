# Purpose: Slack OAuth user_scopes merge 회귀 (Bugbot — chat:write 누락 방지)

from __future__ import annotations

import json
from pathlib import Path


def test_slack_load_client_merges_chat_write(tmp_path, monkeypatch):
    client_file = tmp_path / "slack_oauth_client.json"
    client_file.write_text(json.dumps({
        "client_id": "cid",
        "client_secret": "sec",
        "user_scopes": ["channels:read", "search:read"],
    }), encoding="utf-8")

    monkeypatch.setattr(
        "pipeline.auth.slack.slack_oauth_client_path",
        lambda: client_file,
    )
    from pipeline.auth.slack import _load_client

    scopes = _load_client()["user_scopes"]
    assert "chat:write" in scopes
    assert "channels:read" in scopes
