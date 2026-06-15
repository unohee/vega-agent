# Created: 2026-05-27
# Purpose: File system + shell execution endpoints
# Previously in: web/server.py

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()


from pipeline.path_guard import guard_path as _guard_path


# ── Directory listing ─────────────────────────────────────────────────────────

@router.get("/api/fs/list")
async def fs_list(path: str):
    """One-level directory listing (browser fallback — Tauri uses list_dir command).
    Hidden files excluded; directories sorted first."""
    try:
        p = _guard_path(path)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    if not p.is_dir():
        return JSONResponse({"error": f"폴더가 아님: {path}"}, status_code=400)
    try:
        items = [
            {"name": e.name, "is_dir": e.is_dir()}
            for e in p.iterdir() if not e.name.startswith(".")
        ]
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return JSONResponse({"entries": items})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Shell execution ───────────────────────────────────────────────────────────

@router.post("/api/shell/exec")
async def shell_exec(request: Request):
    """Host shell command triggered by the ! prefix in chat.
    Runs directly on the host, bypassing any sandbox. Uses sid path as cwd if provided.
    body: {command: str, sid?: str, timeout?: int}

    Allowlist/hard-block logic is handled centrally in pipeline.tools_code.host_exec.
    On allowlist miss returns 202 + needs_approval; frontend re-calls with ask="off" after user confirms.
    """
    from web.state import is_remote_allowed as _is_remote_allowed
    if not _is_remote_allowed(request):
        return JSONResponse({"error": "원격 접속에서 호스트 명령 실행은 허용되지 않습니다."}, status_code=403)
    from pipeline.tools_code import host_exec as _host_exec
    body = await request.json()
    cmd = (body.get("command") or "").strip()
    if not cmd:
        return JSONResponse({"error": "command 필수"}, status_code=400)
    timeout = min(int(body.get("timeout") or 30), 120)
    ask = body.get("ask", "on-miss")
    if ask not in ("on-miss", "off", "always"):
        ask = "on-miss"

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _host_exec, cmd, ask, timeout)

    if result.get("__needs_approval__"):
        return JSONResponse(
            {"needs_approval": True, "command": result["command"], "reason": result.get("reason", "")},
            status_code=202,
        )
    if result.get("error"):
        return JSONResponse({"ok": False, "returncode": -1, "stdout": "", "stderr": result["error"]})
    return JSONResponse({
        "ok": result.get("returncode", -1) == 0,
        "returncode": result.get("returncode", -1),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    })


# ── File preview ─────────────────────────────────────────────────────────────

_TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".rtf", ".log", ".rst",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".kt", ".swift",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".rb", ".sh", ".bash", ".zsh",
    ".sql", ".lua", ".vue", ".svelte",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".conf", ".env", ".xml", ".plist",
    ".csv", ".tsv",
    ".html", ".css", ".scss", ".sass", ".less",
    ".dockerfile", ".gitignore", ".cxtignore",
}
_OFFICE_MD_EXTS = {".xlsx", ".xls", ".xlsm", ".xlsb", ".docx", ".pdf"}
_PREVIEW_MAX_BYTES = 1_000_000


@router.get("/api/fs/read")
async def fs_read(path: str):
    """Read a file for preview in the file explorer.
    Text — 1 MB cap, utf-8 decoded.
    xlsx/csv → markdown table.  docx → HTML (mammoth, inline images).
    PDF → text extraction (use /api/fs/download for iframe viewer).
    Images → use /api/fs/read_image.
    """
    try:
        p = _guard_path(path)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    if not p.exists():
        return JSONResponse({"error": f"경로 없음: {path}"}, status_code=404)
    if not p.is_file():
        return JSONResponse({"error": "파일이 아님"}, status_code=400)
    ext = p.suffix.lower()
    size = p.stat().st_size

    if ext in _OFFICE_MD_EXTS:
        try:
            if ext == ".docx":
                from pipeline.tools_google import _docx_to_html
                html = await asyncio.get_event_loop().run_in_executor(
                    None, _docx_to_html, str(p)
                )
                return JSONResponse({
                    "path": str(p), "ext": ext, "size": size,
                    "truncated": False, "kind": "html", "html": html,
                })
            elif ext in {".xlsx", ".xls", ".xlsm", ".xlsb"}:
                from pipeline.tools_google import file_read
                result = await asyncio.get_event_loop().run_in_executor(
                    None, file_read, str(p)
                )
                if isinstance(result, dict) and "error" in result:
                    return JSONResponse({"error": result["error"]}, status_code=500)
                md = result if isinstance(result, str) else str(result)
            elif ext == ".pdf":
                from pipeline.tools_google import _pdf_bytes_to_text
                pdf_bytes = p.read_bytes()
                md = await asyncio.get_event_loop().run_in_executor(
                    None, _pdf_bytes_to_text, pdf_bytes, p.name
                )
            else:
                md = ""
            return JSONResponse({
                "path": str(p), "ext": ext, "size": size,
                "truncated": False, "kind": "markdown", "text": md,
            })
        except Exception as e:
            return JSONResponse({"error": f"{ext} 변환 실패: {e}"}, status_code=500)

    if ext in {".csv", ".tsv"}:
        try:
            from pipeline.tools_google import file_read
            result = await asyncio.get_event_loop().run_in_executor(None, file_read, str(p))
            md = result if isinstance(result, str) else str(result)
            return JSONResponse({
                "path": str(p), "ext": ext, "size": size,
                "truncated": False, "kind": "markdown", "text": md,
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    is_text = ext in _TEXT_EXTS or not ext
    if not is_text:
        return JSONResponse({
            "error": f"미지원 형식: {ext}", "kind": "binary", "size": size,
        }, status_code=415)

    try:
        with open(p, "rb") as f:
            raw = f.read(_PREVIEW_MAX_BYTES + 1)
        truncated = len(raw) > _PREVIEW_MAX_BYTES
        raw = raw[:_PREVIEW_MAX_BYTES]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                return JSONResponse({"error": "텍스트 디코딩 실패", "kind": "binary", "size": size}, status_code=415)
        return JSONResponse({
            "path": str(p), "ext": ext, "size": size,
            "truncated": truncated, "kind": "text", "text": text,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/fs/download")
async def fs_download(path: str):
    """Return the raw file as-is (for preview viewers such as PDF iframe). Rejects files over 50 MB."""
    try:
        p = _guard_path(path)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    if not p.exists() or not p.is_file():
        return JSONResponse({"error": "파일 없음"}, status_code=404)
    if p.stat().st_size > 50 * 1024 * 1024:
        return JSONResponse({"error": "50MB 초과"}, status_code=413)
    ext = p.suffix.lower()
    media_type = {
        ".pdf": "application/pdf",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    }.get(ext, "application/octet-stream")
    return FileResponse(str(p), media_type=media_type, filename=p.name)


@router.post("/api/fs/reveal")
async def fs_reveal(request: Request):
    """Reveal a file or folder in macOS Finder (open -R)."""
    import subprocess
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = (body.get("path") or "").strip()
    if not raw:
        return JSONResponse({"error": "path 필수"}, status_code=400)
    try:
        p = _guard_path(raw)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    if not p.exists():
        return JSONResponse({"error": f"경로 없음: {raw}"}, status_code=404)
    try:
        subprocess.run(["open", "-R", str(p)], check=False, timeout=3)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


_IMG_MEDIA = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".heic": "image/heic", ".heif": "image/heif",
    ".tiff": "image/tiff", ".tif": "image/tiff",
    ".avif": "image/avif",
}
_MAX_IMG_BYTES = 20 * 1024 * 1024


@router.get("/api/fs/read_image")
async def fs_read_image(path: str):
    """Return a local image as base64 — used when attaching a Tauri-dropped image to a GPT vision request."""
    import base64 as _b64
    try:
        p = _guard_path(path)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    if not p.is_file():
        return JSONResponse({"error": f"파일 없음: {path}"}, status_code=400)
    media = _IMG_MEDIA.get(p.suffix.lower())
    if not media:
        return JSONResponse({"error": f"지원 안 하는 이미지 형식: {p.suffix}"}, status_code=400)
    try:
        raw = p.read_bytes()
        if len(raw) > _MAX_IMG_BYTES:
            return JSONResponse({"error": "이미지가 너무 큼 (20MB 초과)"}, status_code=400)
        b64 = _b64.b64encode(raw).decode()
        return JSONResponse({"data": b64, "media_type": media, "name": p.name})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── 외부 에디터로 열기 ────────────────────────────────────────────────────────

_EDITOR_CANDIDATES = ["cursor", "code", "idea", "subl", "zed"]


def _find_editor() -> str | None:
    """PATH에서 사용 가능한 첫 번째 에디터 CLI를 반환."""
    import shutil
    for cmd in _EDITOR_CANDIDATES:
        if shutil.which(cmd):
            return cmd
    return None


@router.post("/api/fs/open_in_editor")
async def fs_open_in_editor(request: Request):
    """파일/폴더를 설치된 코드 에디터로 열기.
    body: {"path": "/abs/path", "editor": "code"(optional)}
    editor 미지정 시 cursor → code → idea → subl → zed 순으로 자동 감지."""
    import subprocess
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = (body.get("path") or "").strip()
    if not raw:
        return JSONResponse({"error": "path 필수"}, status_code=400)
    try:
        p = _guard_path(raw)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    if not p.exists():
        return JSONResponse({"error": f"경로 없음: {raw}"}, status_code=404)

    # INT-1468 H3: editor 는 화이트리스트(_EDITOR_CANDIDATES)로 제한한다.
    # 과거엔 body 값을 검증 없이 Popen 해 임의 로컬 실행파일을 띄울 수 있었다.
    requested = (body.get("editor") or "").strip()
    if requested:
        if requested not in _EDITOR_CANDIDATES:
            return JSONResponse(
                {"error": f"허용되지 않은 에디터: {requested} (허용: {', '.join(_EDITOR_CANDIDATES)})"},
                status_code=400,
            )
        import shutil
        editor = shutil.which(requested)
        if not editor:
            return JSONResponse({"error": f"에디터 미설치: {requested}"}, status_code=404)
    else:
        editor = _find_editor()
    if not editor:
        return JSONResponse({"error": "설치된 에디터를 찾을 수 없음 (cursor/code/idea/subl/zed)"}, status_code=404)

    try:
        subprocess.Popen([editor, str(p)], close_fds=True)
        return JSONResponse({"ok": True, "editor": editor})
    except FileNotFoundError:
        return JSONResponse({"error": f"에디터 실행 불가: {editor}"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/fs/available_editors")
async def fs_available_editors():
    """설치된 에디터 목록 반환 (UI 버튼 표시용)."""
    import shutil
    found = [cmd for cmd in _EDITOR_CANDIDATES if shutil.which(cmd)]
    return JSONResponse({"editors": found})


# ── 파일 접근 정책 (allowlist / denylist) ──────────────────────────────────────

@router.get("/api/fs/access_policy")
async def get_access_policy():
    """현재 접근 정책 조회 — 기본 허용 루트 + 사용자 allow/deny + 하드 차단 목록.
    설정창이 "무엇이 허용/차단되는지" 가시화하는 데 쓴다."""
    from pipeline.path_guard import get_policy
    return JSONResponse(get_policy())


@router.post("/api/fs/access_policy")
async def set_access_policy(request: Request):
    """사용자 allowlist/denylist 저장. body: {allow_roots?: [...], deny_paths?: [...]}.
    하드코딩 시크릿/키 차단은 변경 불가(안전 보장). 저장 즉시 다음 접근부터 반영."""
    from pipeline.path_guard import set_policy
    try:
        body = await request.json()
    except Exception:
        body = {}
    allow = body.get("allow_roots")
    deny = body.get("deny_paths")
    if allow is not None and not isinstance(allow, list):
        return JSONResponse({"error": "allow_roots must be a list"}, status_code=400)
    if deny is not None and not isinstance(deny, list):
        return JSONResponse({"error": "deny_paths must be a list"}, status_code=400)
    return JSONResponse(set_policy(allow_roots=allow, deny_paths=deny))
