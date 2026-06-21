---
title: "pipeline/stt_gateway.py — STT gateway"
tags: [stt, whisper, provider, graceful-failure]
sources: [topics/stt-integration]
updated: 2026-06-02
status: active
---

# pipeline/stt_gateway.py

Common gateway for STT (speech-to-text) providers.

## Supported providers

| provider key | Endpoint | Notes |
|------------|-----------|------|
| `openai` | `api.openai.com/v1/audio/transcriptions` | whisper-1 |
| `groq` | `api.groq.com/openai/v1/audio/transcriptions` | |
| `local` | `localhost:8765/v1/audio/transcriptions` | faster-whisper-server |
| `lmstudio` | `localhost:1234/v1/audio/transcriptions` | |

## Graceful failure pattern

When `local` / `lmstudio` is selected, `is_local_stt_alive()` is called first.
If it is not running, a `LocalSTTUnavailable` exception is raised → the API returns 503 + `{"code": "local_stt_unavailable"}`.
The client JS catches this code and only shows a "local STT not running" toast, while the app keeps working normally.

## Configuration

The `stt` section of `data/llm_providers.json`:
```json
{
  "provider": "openai",
  "model": "whisper-1",
  "language": null,
  "response_format": "text"
}
```
`language: null` = auto-detect, `"ko"` = force Korean.

## API endpoints

- `POST /api/stt` — multipart/form-data (audio field)
- `GET /api/stt/config` — read the current configuration
- `POST /api/stt/config` — change the configuration

## Related

- [[topics/stt-integration]]
