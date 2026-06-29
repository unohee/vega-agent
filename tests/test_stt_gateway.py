# Created: 2026-06-29
# Purpose: STT gateway — OpenRouter(JSON+base64) provider 경로 (INT-2000).
#   사용자별 OpenRouter 키 재사용. OpenRouter 는 multipart 가 아니라 JSON base64 를 받는다.
# Dependencies: pipeline/stt_gateway.py (urlopen mock — 네트워크 없음)
# Test Status: passing

from __future__ import annotations

import base64
import io
import json
from unittest.mock import patch

from pipeline import stt_gateway as stt


def test_openrouter_endpoint_resolved():
    assert stt._resolve_endpoint({"provider": "openrouter"}) == \
        "https://openrouter.ai/api/v1/audio/transcriptions"


def test_openrouter_api_key_env_is_openrouter():
    with patch("pipeline.stt_gateway.os.getenv", lambda k, d="": "or-key" if k == "OPENROUTER_API" else d):
        assert stt._resolve_api_key({"provider": "openrouter"}) == "or-key"


def test_default_provider_is_openrouter():
    """기본 STT provider 가 openrouter — LLM 기본 키 재사용(새 키·CF 불필요)."""
    assert stt._DEFAULT_STT["provider"] == "openrouter"


def test_transcribe_openrouter_sends_json_base64():
    """OpenRouter 경로: multipart 가 아니라 JSON {input_audio.data=base64, model} 전송."""
    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"text": "안녕하세요 회의 시작"}).encode()

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["body"] = req.data
        return _Resp()

    cfg = {"provider": "openrouter", "model": "openai/whisper-large-v3", "language": "ko"}
    with patch("pipeline.stt_gateway.get_stt_config", lambda: cfg), \
         patch("pipeline.stt_gateway._resolve_api_key", lambda c: "or-key"), \
         patch("urllib.request.urlopen", fake_urlopen):
        text = stt.transcribe(b"RIFFxxxxWAVE", filename="clip.wav", language_override=None)

    assert text == "안녕하세요 회의 시작"
    assert captured["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
    assert captured["headers"].get("content-type") == "application/json"
    assert captured["headers"].get("authorization") == "Bearer or-key"
    payload = json.loads(captured["body"])
    assert payload["model"] == "openai/whisper-large-v3"
    assert payload["language"] == "ko"
    assert base64.b64decode(payload["input_audio"]["data"]) == b"RIFFxxxxWAVE"
    assert payload["input_audio"]["format"] == "wav"


def test_transcribe_openai_still_multipart():
    """openai provider 는 기존대로 multipart — JSON 분기에 안 걸림."""
    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"plain text result"

    def fake_urlopen(req, timeout=0):
        captured["ctype"] = {k.lower(): v for k, v in req.headers.items()}.get("content-type", "")
        return _Resp()

    cfg = {"provider": "openai", "model": "whisper-1", "response_format": "text"}
    with patch("pipeline.stt_gateway.get_stt_config", lambda: cfg), \
         patch("pipeline.stt_gateway._resolve_api_key", lambda c: "sk-x"), \
         patch("urllib.request.urlopen", fake_urlopen):
        text = stt.transcribe(b"audio", filename="a.webm")
    assert text == "plain text result"
    assert captured["ctype"].startswith("multipart/form-data")
