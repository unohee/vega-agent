# Created: 2026-06-11
# Purpose: 프론트 리터럴 /api/* fetch 경로 ↔ FastAPI 등록 라우트(메서드 포함) 계약 대조.
#          INT-1473 재발 방지 — 프론트가 부르는 엔드포인트가 백엔드에 없으면(405/404) 빨간불.
# Dependencies: web.server (전체 앱 라우트), web/static/*.html, desktop/dist/*.html
# Test Status: mutation으로 red 확인 (POST /api/onboarding 별칭·/api/memory/settings 부재 시 실패)

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

ROOT = Path(__file__).resolve().parent.parent

# 정적 추출 대상 프론트 파일 (리터럴 fetch 경로만 — 변수로 조립되는 경로는 대상 외)
FRONTEND_FILES = [
    ROOT / "web" / "static" / "chat.html",
    ROOT / "web" / "static" / "dashboard.html",
    ROOT / "web" / "static" / "install_wizard.html",
    ROOT / "desktop" / "dist" / "settings.html",
    ROOT / "desktop" / "dist" / "client-settings.html",
]

# fetch( [base +] '/api/...'), fetch(`/api/.../${id}`) 형태의 리터럴 첫 인자 추출.
# prefix(provBase() + , base + 등)는 식별자/괄호/공백/+ 만 허용.
_FETCH_RE = re.compile(
    r"""fetch\(\s*(?:[\w$@.()\s]+?\+\s*)?(['"`])(/api/[^'"`\s]*)\1"""
)
# 추출된 경로 뒤쪽 옵션 객체에서 method: 'POST' 추출용
_METHOD_RE = re.compile(r"""method\s*:\s*['"](\w+)['"]""")

# 알려진 예외 — 백엔드에 의도적으로 없거나 정적 추출이 문맥을 모르는 경로.
# 추가할 때는 반드시 사유를 주석으로 남길 것.
KNOWN_GAPS: set[tuple[str, str]] = set()


def _extract_calls(html: Path) -> list[tuple[str, str, int]]:
    """(method, path, line_no) 리스트. 경로는 ?query 제거, ${...} → {p} 치환."""
    text = html.read_text(encoding="utf-8")
    out: list[tuple[str, str, int]] = []
    matches = list(_FETCH_RE.finditer(text))
    for i, m in enumerate(matches):
        raw = m.group(2)
        # 리터럴 뒤에 + 가 붙으면(예: '/api/onboarding/' + id) 변수 조립 경로 — 정적 검증 대상 외
        if re.match(r"\s*\+", text[m.end():]):
            continue
        # 옵션 객체 탐색 범위: 이번 fetch( 이후 ~ 다음 fetch( 전까지 (최대 250자)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        window = text[m.end(): min(end, m.end() + 250)]
        mm = _METHOD_RE.search(window)
        method = (mm.group(1) if mm else "GET").upper()
        path = raw.split("?")[0]
        path = re.sub(r"\$\{[^}]*\}", "{p}", path)
        if not path or path == "/api/":
            continue
        line_no = text[: m.start()].count("\n") + 1
        out.append((method, path, line_no))
    return out


@pytest.fixture(scope="module")
def backend_routes():
    """web.server 전체 앱의 (methods, path_regex) 목록 — 라우터 + 앱 직등록 라우트 포함."""
    server = importlib.import_module("web.server")
    routes = []
    for r in server.app.routes:
        if isinstance(r, APIRoute):
            routes.append((r.methods or set(), r.path_regex, r.path))
    assert routes, "web.server.app 라우트가 비어 있음 — import 실패?"
    return routes


def _route_exists(routes, method: str, path: str) -> bool:
    concrete = path.replace("{p}", "x")  # 템플릿 파라미터 → 더미 세그먼트
    for methods, rx, _ in routes:
        if method in methods and rx.match(concrete):
            return True
    return False


def test_frontend_fetch_paths_have_backend_routes(backend_routes):
    """프론트의 모든 리터럴 /api/* fetch가 (메서드 포함) 백엔드 라우트에 존재해야 한다."""
    violations = []
    for html in FRONTEND_FILES:
        assert html.exists(), f"프론트 파일 없음: {html}"
        for method, path, line in _extract_calls(html):
            if (method, path) in KNOWN_GAPS:
                continue
            if not _route_exists(backend_routes, method, path):
                violations.append(f"{html.relative_to(ROOT)}:{line} → {method} {path}")
    assert not violations, (
        "프론트가 부르는 엔드포인트가 백엔드에 없음 (메서드 불일치 = 405 / 경로 부재 = 404):\n  "
        + "\n  ".join(violations)
    )


def test_extractor_catches_known_calls():
    """추출기 자체 가드 — 핵심 호출이 추출되는지 확인 (추출 0건이면 위 테스트가 공허하게 green)."""
    chat_calls = {(m, p) for m, p, _ in _extract_calls(ROOT / "web" / "static" / "chat.html")}
    settings_calls = {(m, p) for m, p, _ in _extract_calls(ROOT / "desktop" / "dist" / "settings.html")}
    # INT-1473의 두 버그 지점이 정확히 감시 대상에 들어있어야 한다.
    assert ("POST", "/api/onboarding") in chat_calls
    assert ("GET", "/api/memory/settings") in settings_calls
    assert ("POST", "/api/memory/settings") in settings_calls
    # 양적 가드 — 추출이 비정상적으로 적으면 regex가 깨진 것
    assert len(chat_calls) >= 10
    assert len(settings_calls) >= 10
