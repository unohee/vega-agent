# Created: 2026-05-31
# Purpose: PyInstaller 진입점 — VEGA Agent FastAPI 백엔드(web.server:app)를 uvicorn 으로 띄운다.
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
# setdefault 가 아니라 무조건 덮어쓴다 — 사용자 환경에 깨진 SSL_CERT_FILE 이 미리
# 설정돼 있어도 번들의 certifi 를 신뢰 루트로 강제하기 위함. (env 누락/오염 양쪽 방어)
try:
    import certifi

    ca_bundle = certifi.where()
    if ca_bundle and os.path.exists(ca_bundle):
        os.environ["SSL_CERT_FILE"] = ca_bundle
        os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle
    else:
        print(f"[vega-backend] certifi cacert.pem 부재: {ca_bundle!r}", file=sys.stderr)
except Exception as _e:
    print(f"[vega-backend] certifi CA 설정 실패(무시): {_e}", file=sys.stderr)

# 작업 디렉터리를 번들 루트로 — 상대 경로 리소스(web/static, data/) 로딩 보장
os.chdir(BUNDLE_ROOT)
sys.path.insert(0, str(BUNDLE_ROOT))

# ── 영속 파일 로깅 ────────────────────────────────────────────────────────────
# 배포본은 콘솔이 없어 버그 추적이 어렵다. 루트 로거에 회전 파일 핸들러를 달아
# 모든 모듈(getLogger(__name__))·uvicorn·예외 트레이스백을 ~/Library/Logs/VEGA/ 에 남긴다.
import logging  # noqa: E402
import logging.handlers  # noqa: E402
import traceback  # noqa: E402
from pipeline.data_paths import log_dir  # noqa: E402

LOG_FILE = log_dir() / "vega-backend.log"

_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_handler.setFormatter(_fmt)

_root = logging.getLogger()
_root.setLevel(logging.INFO)
_root.addHandler(_handler)
# stdout 으로도 계속 보내 LaunchAgent/Rust 리다이렉트 로그와 콘솔 실행 모두 커버.
_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(_fmt)
_root.addHandler(_console)

# uvicorn 자체 로거들도 루트 핸들러를 타게 한다(자기 핸들러 비우고 propagate).
for _n in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _lg = logging.getLogger(_n)
    _lg.handlers.clear()
    _lg.propagate = True

# 잡히지 않은 예외도 파일에 남긴다(배포본에서 침묵 크래시 방지).
def _log_uncaught(exc_type, exc_value, exc_tb):
    logging.getLogger("vega.uncaught").critical(
        "Uncaught exception:\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    )

sys.excepthook = _log_uncaught

logging.getLogger("vega.boot").info(
    "VEGA backend 시작 — bundle_root=%s log_file=%s", BUNDLE_ROOT, LOG_FILE
)

PORT = int(os.environ.get("VEGA_PORT", "8100"))

import uvicorn  # noqa: E402

# vega-agent FastAPI 앱 (web/server.py 의 `app`)
from web.server import app  # noqa: E402

if __name__ == "__main__":
    # log_config=None : uvicorn 의 기본 dictConfig 가 우리 루트 핸들러를 덮어쓰지 않게 한다.
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info", log_config=None)
