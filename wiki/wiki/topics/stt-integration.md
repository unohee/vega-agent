---
title: "STT/Whisper Integration"
tags: [stt, whisper, mediarecorder, graceful-failure]
sources: [entities/stt-gateway]
updated: 2026-06-02
status: active
---

# STT/Whisper Integration

Voice input feature added in v0.1.6. Browser MediaRecorder → `/api/stt` → Whisper API-compatible endpoint.

## Design Principle

**Sidecar graceful failure**: when local STT is not installed/not running, the app keeps working normally.
If the user does not install the runtime separately, the mic button remains clickable but only shows a "local STT not running" toast — no error.

## Browser Side (`chat.html`)

- `MediaRecorder API` — prefers `audio/webm;codecs=opus`, falls back to `audio/webm`
- While recording: red pulse animation
- On completion: Blob → FormData → `POST /api/stt` → text inserted at the cursor position
- `{"code": "local_stt_unavailable"}` response → "local STT not running" toast

## Server Side (`pipeline/stt_gateway.py`)

- `transcribe(audio_bytes, filename, language_override)` — sends multipart via urllib.request
- `is_local_stt_alive()` — probes in the order `/health`, `/v1/models`, `/`
- Cloud providers (openai, groq) are always alive=True

## PyInstaller Bundle Constraint

The whisper library cannot be included in `bin/vega-backend` (94MB) — it has a PyTorch ~2GB dependency.
Bundling local Whisper inside is impractical → the sidecar pattern is the correct choice.

## Per-Provider Configuration Examples

```json
// OpenAI
{ "provider": "openai", "model": "whisper-1", "language": null }

// local faster-whisper-server
{ "provider": "local", "model": "whisper-large-v3-turbo", "language": "ko" }
```

## Related

- [[entities/stt-gateway]]
