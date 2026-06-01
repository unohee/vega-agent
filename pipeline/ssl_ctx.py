# Created: 2026-06-02
# Purpose: 배포 환경(PyInstaller 번들)·개발 환경 어디서든 신뢰 가능한 SSL context를
#   단일 소스로 제공한다. 외부 HTTPS 검증이 깨끗한 사용자 맥에서
#   CERTIFICATE_VERIFY_FAILED 로 죽는 문제(시스템 CA 미탐색)를 막는다.
# Dependencies: certifi (없으면 OS 기본 CA 로 폴백)
#
# SSL 인증 방어 구조 (이중 방어):
#   1차(env) — launcher(bin/vega_backend_launcher.py)가 프로세스 시작 시
#     SSL_CERT_FILE/REQUESTS_CA_BUNDLE 를 certifi.where() 로 무조건 설정한다.
#     검증 결과 ssl._create_default_https_context() 가 이 env 를 존중하므로,
#     context 인자 없는 urlopen(google/chatgpt auth, llm_gateway, tools 등 전부)이
#     자동으로 certifi CA 를 쓴다. → 정상 배포 경로는 이걸로 커버됨.
#   2차(명시) — 이 헬퍼. launcher 를 *안 거치는* 실행(직접 `python -m`, 테스트,
#     다른 진입점)에서는 env 가 없어 OS 기본 CA(번들엔 없음)를 보게 된다.
#     그래서 가장 중요한 LLM 호출 경로(streaming.py)는 env 와 무관하게
#     이 헬퍼로 certifi 를 직접 명시해 어떤 실행 경로에서도 깨지지 않게 한다.
#   → 나머지 urlopen 을 일괄 교체하지 않은 이유: 1차 env 로 이미 커버되고,
#     불필요한 전면 교체는 bloat. 핵심 경로만 2차로 이중화한다.
from __future__ import annotations

import ssl
from functools import lru_cache


@lru_cache(maxsize=1)
def _ca_file() -> str | None:
    """certifi 의 cacert.pem 경로. 번들/개발 환경 모두 importlib.resources 로 해결됨."""
    try:
        import certifi
        return certifi.where()
    except Exception:
        return None


def certified_context() -> ssl.SSLContext:
    """certifi CA 번들을 신뢰 루트로 쓰는 검증용 SSL context.

    certifi 가 있으면 그 cacert.pem 을 명시(시스템 CA 미탐색 환경에서도 동작),
    없으면 OS 기본 context 로 폴백한다. 반환된 context 는 호출자가 재사용해도 안전.
    """
    ca = _ca_file()
    if ca:
        return ssl.create_default_context(cafile=ca)
    return ssl.create_default_context()
