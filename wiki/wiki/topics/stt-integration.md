---
title: "STT/Whisper 통합"
tags: [stt, whisper, mediarecorder, graceful-failure]
sources: [entities/stt-gateway]
updated: 2026-06-02
status: active
---

# STT/Whisper 통합

v0.1.6에서 추가된 음성 입력 기능. 브라우저 MediaRecorder → `/api/stt` → Whisper API 호환 엔드포인트.

## 설계 원칙

**사이드카 graceful failure**: 로컬 STT 미설치/미실행 시 앱은 정상 동작.
사용자가 런타임을 따로 설치하지 않으면 마이크 버튼은 클릭 가능하지만 "로컬 STT 미실행" 토스트만 보이고 에러 없음.

## 브라우저 측 (`chat.html`)

- `MediaRecorder API` — `audio/webm;codecs=opus` 우선, 폴백 `audio/webm`
- 녹음 중: 빨간 펄스 애니메이션
- 완료 시: Blob → FormData → `POST /api/stt` → 텍스트를 커서 위치에 삽입
- `{"code": "local_stt_unavailable"}` 응답 → "로컬 STT 미실행" 토스트

## 서버 측 (`pipeline/stt_gateway.py`)

- `transcribe(audio_bytes, filename, language_override)` — urllib.request로 multipart 전송
- `is_local_stt_alive()` — `/health`, `/v1/models`, `/` 순으로 probe
- cloud 프로바이더(openai, groq)는 항상 alive=True

## PyInstaller 번들 제약

`bin/vega-backend` (94MB)에 whisper 라이브러리 포함 불가 — PyTorch ~2GB 의존.
로컬 Whisper를 번들 안에 넣는 건 비현실적 → 사이드카 패턴이 올바른 선택.

## 프로바이더별 설정 예시

```json
// OpenAI
{ "provider": "openai", "model": "whisper-1", "language": null }

// 로컬 faster-whisper-server
{ "provider": "local", "model": "whisper-large-v3-turbo", "language": "ko" }
```

## 관련

- [[entities/stt-gateway]]
