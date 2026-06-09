from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

MAX_AUDIO_SIZE = 25 * 1024 * 1024  # 25 MB (Whisper API limit)


@router.post("/api/stt")
async def stt_transcribe(request: Request):
    """음성 파일 STT 변환. multipart/form-data, 'file' 필드 (webm/mp4/wav/ogg/mp3/flac)."""
    form = await request.form()
    upload = form.get("file")
    if upload is None:
        return JSONResponse({"error": "file 필드가 없습니다"}, status_code=400)

    raw = await upload.read()
    if len(raw) > MAX_AUDIO_SIZE:
        return JSONResponse(
            {"error": f"오디오 파일이 너무 큽니다 ({len(raw)//1024//1024}MB, 최대 25MB)"},
            status_code=413,
        )

    filename = getattr(upload, "filename", None) or "audio.webm"
    language_override = (form.get("language") or "").strip() or None

    try:
        from pipeline.stt_gateway import transcribe as _transcribe, LocalSTTUnavailable
        text = _transcribe(raw, filename=filename, language_override=language_override)
        return JSONResponse({"text": text})
    except LocalSTTUnavailable as e:
        return JSONResponse({"error": str(e), "code": "local_stt_unavailable"}, status_code=503)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": f"STT 처리 실패: {e}"}, status_code=500)


@router.get("/api/stt/config")
async def stt_get_config():
    from pipeline.stt_gateway import get_stt_config
    return JSONResponse(get_stt_config())


@router.post("/api/stt/config")
async def stt_set_config(request: Request):
    from pipeline.stt_gateway import set_stt_config
    body = await request.json()
    allowed = {"provider", "model", "language", "response_format", "endpoint", "api_key_env"}
    cleaned = {k: v for k, v in body.items() if k in allowed}
    set_stt_config(cleaned)
    return JSONResponse({"ok": True})
