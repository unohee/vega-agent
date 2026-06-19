#!/usr/bin/env python3
# Created: 2026-06-19
# Purpose: 배포된 자동 업데이트 매니페스트(latest.json) + updater 자산(tar.gz/setup.exe)을
#   공개 도메인에서 실제로 검증한다. "업데이트가 안 받아짐" 회귀를 CI 에서 잡기 위함:
#   - latest.json 이 공개 GET 200 으로 서빙되는가
#   - 요청한 플랫폼이 모두 있고, 각 signature 가 비어있지 않은가
#   - 각 자산 url 이 v<version>/ 을 가리키고 HTTP 200 으로 받아지는가
# Usage:
#   python3 scripts/verify_update_manifest.py <version> [--platforms a,b] [--base URL] [--no-version-match]
# CI: release-dmg.yml(darwin), build-windows.yml(windows) 가 업로드 직후 호출.
from __future__ import annotations

import argparse
import json
import sys
import time

import requests

DEFAULT_BASE = "https://download.intrect.io/vega/updates"


# 표준 urllib 은 download.intrect.io(Cloudflare 봇 보호)에 403 으로 막힌다(urllib 의 TLS
# fingerprint 를 봇으로 판정 — 2026-06-19 실측: urllib 403 / curl·requests·reqwest 200).
# 실제 updater(reqwest)와 같은 "정상 클라이언트" 조건으로 검증하기 위해 requests 를 쓴다.
def _get(url: str, timeout: float = 20.0, retries: int = 6, head: bool = False):
    """공개 GET/HEAD — R2/CF 전파 지연 대비 재시도(자산 HEAD 가 막 업로드돼 늦을 수 있음).
    HTTP 4xx/5xx 도 전파 지연일 수 있으니 200/3xx 가 아니면 재시도. (status, body) 반환."""
    last = None
    for i in range(retries):
        try:
            if head:
                r = requests.head(url, timeout=timeout, allow_redirects=True)
                if r.status_code < 400:
                    return r.status_code, None
                last = f"HTTP {r.status_code}"
            else:
                r = requests.get(url, timeout=timeout)
                if r.status_code < 400:
                    return r.status_code, r.content
                last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001 — 네트워크 전부 재시도 대상
            last = e
        if i < retries - 1:
            time.sleep(8)
    # 마지막 시도 결과(상태 코드)를 그대로 반환 — 호출부가 4xx/5xx 를 에러로 기록.
    if isinstance(last, str) and last.startswith("HTTP "):
        return int(last.split()[1]), None
    raise RuntimeError(f"{url} 접근 실패({retries}회): {last}")


def verify(version: str, platforms: list[str], base: str, version_match: bool) -> list[str]:
    errors: list[str] = []
    manifest_url = base.rstrip("/") + "/latest.json"
    try:
        _, body = _get(manifest_url)
        data = json.loads(body)
    except Exception as e:  # noqa: BLE001
        return [f"latest.json 조회/파싱 실패: {e}"]

    top_ver = data.get("version")
    if version_match and top_ver != version:
        errors.append(f"latest.json version '{top_ver}' != 기대 '{version}'")

    plats = data.get("platforms") or {}
    for p in platforms:
        entry = plats.get(p)
        if not entry:
            errors.append(f"플랫폼 누락: {p}")
            continue
        if not entry.get("signature"):
            errors.append(f"{p}: signature 가 비어있음")
        url = entry.get("url", "")
        if f"/v{version}/" not in url:
            errors.append(f"{p}: 자산 url 이 v{version} 를 가리키지 않음: {url}")
        try:
            code, _ = _get(url, timeout=30, head=True)
            if code != 200:
                errors.append(f"{p}: 자산 HTTP {code}: {url}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p}: 자산 접근 실패: {e}")
    return errors


def main() -> int:
    # Windows 콘솔(cp1252/cp949)에서 ✓/✗·한국어 print 가 UnicodeEncodeError 로 죽어
    # 검증이 통과여도 step 이 실패하던 것 방지 (make_latest_json 과 동일 함정).
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="배포된 업데이트 매니페스트 검증")
    ap.add_argument("version", help="기대 버전 (예: 0.1.40)")
    ap.add_argument("--platforms", default="darwin-aarch64,darwin-x86_64",
                    help="검증할 플랫폼 키 (쉼표 구분)")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--no-version-match", action="store_true",
                    help="latest.json top-level version 일치 검사 생략(병합 시나리오)")
    args = ap.parse_args()
    plats = [p.strip() for p in args.platforms.split(",") if p.strip()]
    errors = verify(args.version, plats, args.base, not args.no_version_match)
    if errors:
        print("✗ 업데이트 매니페스트 검증 실패:")
        for e in errors:
            print("  -", e)
        return 1
    print(f"✓ 업데이트 매니페스트 검증 통과: v{args.version}, 플랫폼={plats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
