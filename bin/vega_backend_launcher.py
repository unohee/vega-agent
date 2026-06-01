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

# 배포된 PyInstaller 앱은 새 사용자 맥에서 시스템 CA 경로를 못 찾는 경우가 있다.
# 프로세스 시작 시 certifi 번들 경로를 표준 SSL env에 고정해 모든 HTTPS 클라이언트가 공유하게 한다.
try:
    import certifi

    ca_bundle = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", ca_bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)
except Exception:
    pass

# 작업 디렉터리를 번들 루트로 — 상대 경로 리소스(web/static, data/) 로딩 보장
os.chdir(BUNDLE_ROOT)
sys.path.insert(0, str(BUNDLE_ROOT))

PORT = int(os.environ.get("VEGA_PORT", "8100"))

import uvicorn  # noqa: E402

# vega-core FastAPI 앱 (web/server.py 의 `app`)
from web.server import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
