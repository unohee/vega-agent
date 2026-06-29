#!/usr/bin/env python3
# Created: 2026-06-08 / Updated: 2026-06-14 (Windows updater + R2 병합, INT-1372/1506 후속)
# Purpose: Tauri updater 매니페스트(latest.json) 생성·병합.
#   build_output/updater/ 또는 build_output/ 에 만들어진 updater 아티팩트(.sig)를 읽어
#   platform 항목(darwin-aarch64 / darwin-x86_64 / windows-x86_64)을 채운다.
#   CI(release-dmg.yml / build-windows.yml)가 이 파일 + 아티팩트를 R2(download.intrect.io)에
#   올리고, updater endpoint 를 그 R2 의 latest.json URL 로 두면 자동 업데이트가 동작한다.
# Dependencies: stdlib only
#
# 사용:
#   python3 scripts/make_latest_json.py <version> [--macos | --windows] [--merge-from <기존 latest.json>]
#     --macos    : darwin-aarch64 / darwin-x86_64 항목만 채운다 (build_output/updater/*.app.tar.gz)
#     --windows  : windows-x86_64 항목만 채운다 (build_output/*-setup.exe)
#     (둘 다 생략하면 가능한 플랫폼을 전부 시도 — 단일 호스트가 모두 빌드한 경우)
#     --merge-from <path> : 기존 매니페스트를 읽어 병합. 이번 호출이 채우지 않은
#                           다른-플랫폼 항목은 버전이 달라도 보존한다(분리 빌드가 서로
#                           상대 플랫폼을 지우지 않도록 — INT-1991 후속).
#
# 출력: build_output/latest.json
#
# 왜 병합인가: macOS(self-hosted)와 Windows(github-hosted) 빌드가 분리돼 있어 각자 R2 의
#   같은 latest.json 을 쓴다. 병합 없이 덮어쓰면 서로 상대 플랫폼 항목을 지운다.
#   각 워크플로가 R2 의 현재 latest.json 을 --merge-from 으로 넘겨 자기 플랫폼만 갱신한다.
#
# url 필드는 R2 공개 다운로드 URL 로 채운다(download.intrect.io).
#   리포가 private 이라 GitHub Release URL 은 updater 익명 GET 으로 접근 불가 —
#   반드시 공개 프록시(R2)를 거쳐야 한다. base 는 VEGA_UPDATE_BASE_URL 로 오버라이드.

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Windows 콘솔 기본 인코딩(cp949/cp1252)에선 ✓·→·한글 print 가 UnicodeEncodeError 로
# 죽어 스크립트가 비0 종료 → CI 스텝 실패가 된다. stdout/stderr 를 UTF-8 로 고정한다.
# (파일 쓰기는 이미 encoding="utf-8" 이라 무관 — 죽는 건 콘솔 출력뿐)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # py3.7+
    except (AttributeError, ValueError):
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO_ROOT / "build_output"
UPDATER_DIR = BUILD_DIR / "updater"

# R2 공개 베이스 — tauri.conf.json updater.endpoints 와 같은 호스트/경로를 가리켜야 한다.
# 버전별 디렉터리(vega/updates/v<ver>/) 아래에 아티팩트를 둔다.
DEFAULT_UPDATE_BASE = "https://download.intrect.io/vega/updates"

# (Tauri platform key, 아티팩트 파일명 템플릿, 탐색 디렉터리)
#   macOS: build_dmg.sh 가 staple 된 .app 을 tar.gz 로 만들고 cargo tauri signer sign.
#   Windows: Tauri v2 가 NSIS setup.exe 를 updater 로 재사용하고 setup.exe.sig 를 생성
#            (별도 .nsis.zip 아님 — v2 동작, 공식 문서 확인).
_MACOS_PLATFORMS = [
    ("darwin-aarch64", "VEGA-{ver}-aarch64.app.tar.gz", UPDATER_DIR),
    ("darwin-x86_64", "VEGA-{ver}-x86_64.app.tar.gz", UPDATER_DIR),
]
_WINDOWS_PLATFORMS = [
    # NSIS 인스톨러는 productName_version_arch-setup.exe 형식 (VEGA_0.1.23_x64-setup.exe).
    ("windows-x86_64", "VEGA_{ver}_x64-setup.exe", BUILD_DIR),
]


def _r2_url(base: str, version: str, fname: str) -> str:
    return f"{base.rstrip('/')}/v{version}/{fname}"


def _collect(specs, version: str, base: str) -> dict[str, dict]:
    """주어진 platform spec 들에서 아티팩트(+.sig)가 존재하는 항목만 매니페스트 dict 로."""
    out: dict[str, dict] = {}
    for plat_key, fname_tmpl, search_dir in specs:
        fname = fname_tmpl.format(ver=version)
        artifact = search_dir / fname
        sig = search_dir / (fname + ".sig")
        if not artifact.exists() or not sig.exists():
            print(f"[skip] {plat_key}: {fname}(.sig) 없음 ({search_dir})", file=sys.stderr)
            continue
        out[plat_key] = {
            "signature": sig.read_text(encoding="utf-8").strip(),
            "url": _r2_url(base, version, fname),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--macos", action="store_true", help="darwin 항목만")
    ap.add_argument("--windows", action="store_true", help="windows 항목만")
    ap.add_argument("--merge-from", default=None, help="병합할 기존 latest.json 경로")
    args = ap.parse_args()

    version = args.version.lstrip("v").strip()
    base = os.environ.get("VEGA_UPDATE_BASE_URL", DEFAULT_UPDATE_BASE)

    # 어떤 플랫폼을 이 호출에서 채울지 결정
    if args.macos and not args.windows:
        specs = _MACOS_PLATFORMS
    elif args.windows and not args.macos:
        specs = _WINDOWS_PLATFORMS
    else:
        specs = _MACOS_PLATFORMS + _WINDOWS_PLATFORMS

    platforms = _collect(specs, version, base)

    # 기존 매니페스트와 병합 — 이번 호출이 *생성하지 않은* 다른-플랫폼 항목은
    # 버전이 달라도 보존한다. macOS(self-hosted)와 Windows(github-hosted) 빌드가
    # 분리돼 있어, 버전 bump 시 먼저 도는 빌드가 상대 플랫폼의 직전 항목을 버리면
    # latest.json 이 한쪽 플랫폼만 남아 그 플랫폼 자동업데이트가 깨진다(INT-1991 후속).
    # → 상대 플랫폼은 직전 버전 url 그대로 유지하다가 그쪽 빌드가 돌면 갱신된다.
    #   top-level version 은 항상 이번 버전. 이번 호출이 채운 platform 만 덮어쓴다.
    if args.merge_from:
        merge_path = Path(args.merge_from)
        if merge_path.exists():
            try:
                prev = json.loads(merge_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"::warning::기존 latest.json 파싱 실패({e}) — 병합 생략", file=sys.stderr)
                prev = {}
            prev_platforms = dict(prev.get("platforms") or {})
            # 이번 호출이 만든 platform 은 새 값으로, 나머지(상대 플랫폼)는 기존값 보존.
            preserved = {k: v for k, v in prev_platforms.items() if k not in platforms}
            merged = dict(preserved)
            merged.update(platforms)
            platforms = merged
            print(f"[merge] 갱신: {set(p for p in platforms if p not in preserved) or '없음'} · "
                  f"보존(타플랫폼, prev v{prev.get('version')}): {set(preserved) or '없음'}",
                  file=sys.stderr)
        else:
            print(f"[merge] {merge_path} 없음 — 신규 매니페스트로 생성", file=sys.stderr)

    if not platforms:
        print("::error::updater 아티팩트가 하나도 없습니다 — latest.json 생성 불가", file=sys.stderr)
        return 1

    import datetime
    pub_date = os.environ.get("VEGA_PUB_DATE") or datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "version": version,
        "notes": f"VEGA v{version}",
        "pub_date": pub_date,
        "platforms": platforms,
    }

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / "latest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {out} 생성 (platforms: {', '.join(platforms.keys())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
