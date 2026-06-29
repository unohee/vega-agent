# Created: 2026-06-02
# Purpose: STT (Speech-to-Text) gateway — routes audio files to a Whisper-compatible transcription endpoint.
#   Reads config from llm_providers.json's "stt" section.
#   Compatible providers: OpenAI (whisper-1), local Whisper (whisperkit/faster-whisper),
#   LM Studio (if it gains audio support), OpenRouter, Groq.
# Dependencies: pipeline/llm_gateway.py, pipeline/keychain.py
# Test Status: manual

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pipeline.data_paths import llm_providers_path as _llm_providers_path, repo_data_dir as _repo_data_dir

_PROVIDERS_PATH = _llm_providers_path()
_REPO_PROVIDERS_PATH = _repo_data_dir() / "llm_providers.json"

# Default STT config used when no "stt" section is present
class LocalSTTUnavailable(RuntimeError):
    """Raised when provider=local but the sidecar process is not reachable."""


# 기본은 openrouter — 사용자가 LLM 용으로 이미 가진 OpenRouter 키를 그대로 재사용한다.
# (별도 STT 키·CF·셀프호스팅 불필요. INT-2000.) 화자분리 회의 모드는 별도(self-host PoC).
_DEFAULT_STT = {
    "provider": "openrouter",
    "model": "openai/whisper-large-v3",
    "language": None,   # None → auto-detect
    "response_format": "text",
}

# Well-known STT endpoints keyed by provider name (OpenAI-compatible Whisper API)
_WELL_KNOWN_ENDPOINTS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1/audio/transcriptions",
    "openai":   "https://api.openai.com/v1/audio/transcriptions",
    "groq":     "https://api.groq.com/openai/v1/audio/transcriptions",
    "local":    "http://localhost:8765/v1/audio/transcriptions",  # cxt-ignore: fake_data  # e.g. faster-whisper-server
    "lmstudio": "http://localhost:1234/v1/audio/transcriptions",  # cxt-ignore: fake_data
}

# OpenRouter 의 transcription API 는 multipart 가 아니라 JSON+base64 를 받는다
# (input_audio.data = base64, model = openai/whisper-* 형식). 다른 OpenAI 호환
# 서버(openai/groq/local)는 multipart 라 분기한다.
_JSON_B64_PROVIDERS = {"openrouter"}


def _read_config() -> dict:
    for path in (_PROVIDERS_PATH, _REPO_PROVIDERS_PATH):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {}


def get_stt_config() -> dict:
    """Returns the active STT configuration dict."""
    cfg = _read_config()
    return dict(cfg.get("stt") or _DEFAULT_STT)


def set_stt_config(stt_cfg: dict) -> None:
    """Persists the STT configuration into llm_providers.json."""
    for path in (_PROVIDERS_PATH, _REPO_PROVIDERS_PATH):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["stt"] = stt_cfg
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(path)
                return
            except Exception:
                continue
    # If neither file exists, write to user data path
    _PROVIDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PROVIDERS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"stt": stt_cfg}, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_PROVIDERS_PATH)


def _resolve_endpoint(stt_cfg: dict) -> str:
    """Returns the transcription URL for the given STT config."""
    if "endpoint" in stt_cfg:
        return stt_cfg["endpoint"]
    provider_name = stt_cfg.get("provider", "openai")
    if provider_name in _WELL_KNOWN_ENDPOINTS:
        return _WELL_KNOWN_ENDPOINTS[provider_name]
    # Try to look up base_url from main providers section
    main_cfg = _read_config()
    prov = (main_cfg.get("providers") or {}).get(provider_name, {})
    base = prov.get("base_url", "")
    if base:
        return base.rstrip("/") + "/audio/transcriptions"
    return _WELL_KNOWN_ENDPOINTS["openai"]


def _resolve_api_key(stt_cfg: dict) -> str:
    """Returns the API key for the STT provider."""
    # Explicit key in config takes precedence
    if "api_key" in stt_cfg:
        return stt_cfg["api_key"]

    provider_name = stt_cfg.get("provider", "openai")
    api_key_env = stt_cfg.get("api_key_env", "")

    # Try provider-specific env var
    if not api_key_env:
        _env_map = {
            "openai":   "OPENAI_API_KEY",
            "groq":     "GROQ_API_KEY",
            "openrouter": "OPENROUTER_API",
            "local":    "",
            "lmstudio": "",
        }
        api_key_env = _env_map.get(provider_name, "OPENAI_API_KEY")

    if not api_key_env:
        return ""  # local provider, no key needed

    key = os.getenv(api_key_env, "")
    if not key:
        from pipeline import keychain
        key = keychain.get_secret(api_key_env) or ""
    if not key:
        # Try main providers section for the same provider
        main_cfg = _read_config()
        prov = (main_cfg.get("providers") or {}).get(provider_name, {})
        main_key_env = prov.get("api_key_env", "")
        if main_key_env:
            key = os.getenv(main_key_env, "")
            if not key:
                key = keychain.get_secret(main_key_env) or ""
    return key


_LOCAL_PROVIDERS = {"local", "lmstudio"}


def is_local_stt_alive(stt_cfg: dict | None = None, timeout: float = 1.5) -> bool:
    """Returns True if the local STT sidecar is reachable (GET /health or /v1/models).

    Used to gate the /api/stt endpoint — if the local provider is configured but the
    sidecar is not running, the server returns 503 immediately rather than hanging.
    """
    cfg = stt_cfg or get_stt_config()
    if cfg.get("provider") not in _LOCAL_PROVIDERS:
        return True  # cloud providers are always considered available
    endpoint = _resolve_endpoint(cfg)
    base = endpoint.rsplit("/audio/", 1)[0]  # strip /audio/transcriptions
    import urllib.request
    for path in ("/health", "/v1/models", "/"):
        try:
            req = urllib.request.Request(base + path, method="GET")
            with urllib.request.urlopen(req, timeout=timeout):
                return True
        except Exception:
            continue
    return False


def transcribe(
    audio_bytes: bytes,
    filename: str = "audio.webm",
    language_override: str | None = None,
) -> str:
    """Transcribes audio_bytes using the configured STT provider.

    language_override: if set, overrides stt_cfg["language"] for this call only.
    Raises LocalSTTUnavailable if provider=local and the sidecar is not running.
    Raises RuntimeError on other failures.
    """
    import urllib.request
    import urllib.error

    stt_cfg = get_stt_config()

    if stt_cfg.get("provider") in _LOCAL_PROVIDERS and not is_local_stt_alive(stt_cfg):
        raise LocalSTTUnavailable(
            f"로컬 STT 사이드카가 실행 중이지 않습니다 ({_resolve_endpoint(stt_cfg)}). "
            "faster-whisper-server 또는 whisper.cpp 서버를 먼저 시작하거나, "
            "STT 설정에서 다른 프로바이더(openai, groq)를 선택하세요."
        )
    endpoint = _resolve_endpoint(stt_cfg)
    api_key = _resolve_api_key(stt_cfg)
    model = stt_cfg.get("model", "whisper-1")
    language = language_override or stt_cfg.get("language") or None
    response_format = stt_cfg.get("response_format", "text")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "webm"

    # OpenRouter: JSON + base64 (multipart 아님). 사용자별 OpenRouter 키 재사용.
    if stt_cfg.get("provider") in _JSON_B64_PROVIDERS or "openrouter.ai" in endpoint:
        import base64
        payload: dict = {
            "model": model,
            "input_audio": {"data": base64.b64encode(audio_bytes).decode(), "format": ext},
        }
        if language:
            payload["language"] = language
        jbody = json.dumps(payload).encode()
        jheaders = {"Content-Type": "application/json", "Content-Length": str(len(jbody))}
        if api_key:
            jheaders["Authorization"] = f"Bearer {api_key}"
        jreq = urllib.request.Request(endpoint, data=jbody, headers=jheaders, method="POST")
        try:
            with urllib.request.urlopen(jreq, timeout=90) as resp:
                raw = resp.read().decode("utf-8").strip()
                try:
                    return json.loads(raw).get("text", raw)
                except Exception:
                    return raw
        except urllib.error.HTTPError as e:
            body_err = e.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"STT HTTP {e.code}: {body_err}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"STT 연결 실패 ({endpoint}): {e.reason}") from e

    # Build multipart/form-data manually (no external deps) — openai/groq/local
    boundary = "----VegaSTTBoundary"
    parts: list[bytes] = []

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    def _file_field(name: str, fname: str, data: bytes, mime: str) -> bytes:
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
        return header + data + b"\r\n"

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "webm"
    _mime_map = {
        "webm": "audio/webm",
        "mp4":  "audio/mp4",
        "m4a":  "audio/mp4",
        "wav":  "audio/wav",
        "ogg":  "audio/ogg",
        "mp3":  "audio/mpeg",
        "flac": "audio/flac",
    }
    mime = _mime_map.get(ext, "audio/webm")

    parts.append(_file_field("file", filename, audio_bytes, mime))
    parts.append(_field("model", model))
    parts.append(_field("response_format", response_format))
    if language:
        parts.append(_field("language", language))

    body = b"".join(parts) + f"--{boundary}--\r\n".encode()

    headers: dict[str, str] = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8").strip()
            # response_format="text" → plain string
            # response_format="json" → {"text": "..."}
            if response_format == "json":
                try:
                    return json.loads(raw).get("text", raw)
                except Exception:
                    return raw
            return raw
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"STT HTTP {e.code}: {body_err}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"STT 연결 실패 ({endpoint}): {e.reason}") from e
