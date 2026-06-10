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

# Windows 콘솔 기본 인코딩(cp1252 등)에선 한국어 로그가 UnicodeEncodeError 로
# "--- Logging error ---" 를 양산한다. stdout/stderr 를 UTF-8 로 강제 (INT-1438).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass  # 파이프/리다이렉트 등 reconfigure 미지원 스트림은 무시

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


def _wait_port_free(port: int) -> None:
    """다른 백엔드가 이미 포트를 서빙 중이면 뺏지 않고 빌 때까지 양보 대기한다.

    와일드카드(0.0.0.0) 바인드와 특정 주소(127.0.0.1) 바인드는 EADDRINUSE 없이
    공존할 수 있다. 그 상태에선 localhost 연결이 특정 바인드 쪽으로 쏠려, 서로 다른
    DB를 쓰는 두 백엔드가 트래픽을 나눠 받는 split-brain이 된다 — 사용자는
    "세션 컨텍스트가 통째로 사라짐"으로 체감한다 (INT-1439 실사고: 개인 VEGA
    데브 데몬(*:8100, vega.db) 위에 배포 백엔드(127.0.0.1:8100, agent.db)가
    조용히 겹쳐 기동). launchd KeepAlive=true 라 exit 하면 respawn 루프가 되므로
    프로세스 안에서 대기한다.
    """
    import socket
    import time
    import urllib.request

    boot_log = logging.getLogger("vega.boot")
    notified = False
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass
        except OSError:
            # 연결 불가 = 포트 비어있음(또는 일시 오류) — 바인드 시도로 진행.
            if notified:
                boot_log.info("포트 %d 해제 감지 — 기동 재개", port)
            return
        if not notified:
            notified = True
            holder = ""
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=3
                ) as r:
                    holder = r.read(2048).decode("utf-8", "replace")
            except Exception:
                holder = "(health 응답 없음 — VEGA 계열 아닐 수 있음)"
            boot_log.warning(
                "포트 %d 를 다른 프로세스가 이미 서빙 중 — split-brain 방지를 위해 "
                "해제될 때까지 대기한다. holder health=%s", port, holder[:300]
            )
        time.sleep(5)


import uvicorn  # noqa: E402

# vega-agent FastAPI 앱 (web/server.py 의 `app`)
from web.server import app  # noqa: E402

if __name__ == "__main__":
    _wait_port_free(PORT)
    # log_config=None : uvicorn 의 기본 dictConfig 가 우리 루트 핸들러를 덮어쓰지 않게 한다.
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info", log_config=None)
