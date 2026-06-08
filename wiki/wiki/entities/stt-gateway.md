---
title: "pipeline/stt_gateway.py — STT 게이트웨이"
tags: [stt, whisper, provider, graceful-failure]
sources: [topics/stt-integration]
updated: 2026-06-02
status: active
---

# pipeline/stt_gateway.py

STT(음성→텍스트) 프로바이더 공통 게이트웨이.

## 지원 프로바이더

| provider 키 | 엔드포인트 | 비고 |
|------------|-----------|------|
| `openai` | `api.openai.com/v1/audio/transcriptions` | whisper-1 |
| `groq` | `api.groq.com/openai/v1/audio/transcriptions` | |
| `local` | `localhost:8765/v1/audio/transcriptions` | faster-whisper-server |
| `lmstudio` | `localhost:1234/v1/audio/transcriptions` | |

## Graceful Failure 패턴

`local` / `lmstudio` 선택 시 `is_local_stt_alive()` 먼저 호출.
미실행이면 `LocalSTTUnavailable` 예외 → API에서 503 + `{"code": "local_stt_unavailable"}` 반환.
클라이언트 JS는 이 코드를 잡아 "로컬 STT 미실행" 토스트만 표시하고 앱은 정상 동작.

## 설정

`data/llm_providers.json`의 `stt` 섹션:
```json
{
  "provider": "openai",
  "model": "whisper-1",
  "language": null,
  "response_format": "text"
}
```
`language: null` = 자동 감지, `"ko"` = 한국어 강제.

## API 엔드포인트

- `POST /api/stt` — multipart/form-data (audio 필드)
- `GET /api/stt/config` — 현재 설정 조회
- `POST /api/stt/config` — 설정 변경

## 관련

- [[topics/stt-integration]]
