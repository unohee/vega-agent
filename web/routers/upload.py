from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _uploads_dir() -> Path:
    try:
        from pipeline.data_paths import data_dir
        p = data_dir() / "uploads"
    except Exception:
        p = Path(__file__).parent.parent.parent / "data" / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


_UPLOAD_DIR = _uploads_dir()

MAX_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_AUDIO_SIZE = 25 * 1024 * 1024  # 25 MB (Whisper API limit)


@router.post("/api/upload")
async def upload_file(request: Request):
    """파일 업로드 — data/uploads/ 저장 후 경로 반환. 암호화 Office 파일 자동 복호화."""
    import io, uuid as _uuid

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        return JSONResponse({"error": "file 필드가 없습니다"}, status_code=400)

    raw = await upload.read()
    if len(raw) > MAX_SIZE:
        return JSONResponse({"error": f"파일이 너무 큽니다 ({len(raw)//1024//1024}MB)"}, status_code=413)

    fname = upload.filename or "unknown"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    password = (form.get("password") or "").strip() or None

    if ext in ("xlsx", "xlsm", "xltx", "xls", "xlsb", "ods", "docx", "doc", "pptx", "ppt"):
        try:
            import msoffcrypto
            of = msoffcrypto.OfficeFile(io.BytesIO(raw))
            if of.is_encrypted():
                if not password:
                    return JSONResponse({"error": "password_required"}, status_code=401)
                try:
                    out = io.BytesIO()
                    of.load_key(password=password)
                    of.decrypt(out)
                    raw = out.getvalue()
                except Exception:
                    return JSONResponse({"error": "비밀번호가 틀렸습니다"}, status_code=403)
        except Exception:
            pass

    safe_name = f"{_uuid.uuid4().hex[:8]}_{fname}"
    dest = _UPLOAD_DIR / safe_name
    dest.write_bytes(raw)
    return JSONResponse({"filename": fname, "path": str(dest)})


@router.post("/api/upload/image")
async def upload_image_base64(request: Request):
    """base64 이미지 저장 → 경로 반환."""
    import base64 as _b64
    import uuid as _uuid

    body = await request.json()
    data_str = body.get("data", "")
    media_type = body.get("media_type", "image/png")
    name = body.get("name", "image.png")

    if not data_str:
        return JSONResponse({"error": "data 필드가 없습니다"}, status_code=400)

    # 크기 제한 (INT-2231): multipart 경로의 MAX_SIZE 가 base64 경로엔 적용 안 돼,
    # 거대 페이로드로 메모리/디스크 소진이 가능했다. 디코드 전후로 차단.
    if len(data_str) > 28 * 1024 * 1024:  # ~20MB raw 의 base64 상한
        return JSONResponse({"error": "이미지가 너무 큽니다 (최대 20MB)"}, status_code=413)

    ext_map = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
               "image/webp": "webp", "image/gif": "gif"}
    ext = ext_map.get(media_type, "png")
    safe_name = f"{_uuid.uuid4().hex[:8]}_{name}"
    if not safe_name.endswith(f".{ext}"):
        safe_name = safe_name.rsplit(".", 1)[0] + f".{ext}"

    raw = _b64.b64decode(data_str)
    if len(raw) > 20 * 1024 * 1024:
        return JSONResponse({"error": "이미지가 너무 큽니다 (최대 20MB)"}, status_code=413)
    dest = _UPLOAD_DIR / safe_name
    dest.write_bytes(raw)
    return JSONResponse({"path": str(dest), "filename": safe_name})
