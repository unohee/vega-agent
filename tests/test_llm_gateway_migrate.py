# Purpose: llm_gateway config migration — parse/write 분리 회귀 (Bugbot)

from __future__ import annotations

import json
from pathlib import Path


def test_read_config_returns_migrated_even_if_write_fails(tmp_path, monkeypatch):
    providers = tmp_path / "llm_providers.json"
    providers.write_text(json.dumps({
        "active": "chatgpt",
        "providers": {
            "chatgpt": {
                "default_model": "gpt-4o",
                "base_url": "https://chatgpt.com/backend-api/codex/responses",
            }
        },
    }), encoding="utf-8")

    import pipeline.llm_gateway as gw

    monkeypatch.setattr(gw, "_PROVIDERS_PATH", providers)
    monkeypatch.setattr(gw, "_write_config", lambda _cfg: (_ for _ in ()).throw(OSError("disk full")))

    cfg = gw._read_config()
    assert cfg["providers"]["chatgpt"]["default_model"] == "gpt-5.5"
