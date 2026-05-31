# Created: 2026-05-31
# Purpose: PyInstaller 진입점 — VEGA Core FastAPI 백엔드(web.server:app)를 uvicorn 으로 띄운다.
#   sys._MEIPASS(번들 루트)를 cwd 로 잡아 web/static, data/ 기본값을 찾게 한다.
# Dependencies: web/server.py, uvicorn
import os
import sys
from pathlib import Path

# PyInstaller 번들 루트 (onefile=_MEIPASS, onedir=실행파일 디렉터리)
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
os.environ.setdefault("VEGA_BUNDLE_ROOT", str(BUNDLE_ROOT))

# 작업 디렉터리를 번들 루트로 — 상대 경로 리소스(web/static, data/) 로딩 보장
os.chdir(BUNDLE_ROOT)
sys.path.insert(0, str(BUNDLE_ROOT))

PORT = int(os.environ.get("VEGA_PORT", "8100"))

import uvicorn  # noqa: E402

# vega-core FastAPI 앱 (web/server.py 의 `app`)
from web.server import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
