#!/usr/bin/env python3
# Created: 2026-06-08
# Purpose: Tauri updater 매니페스트(latest.json) 생성.
#   build_dmg.sh 가 build_output/updater/ 에 만든 .app.tar.gz(.sig) 들을 읽어
#   darwin-aarch64 / darwin-x86_64 platform 항목을 채운다. CI(release-dmg.yml)가
#   이 파일을 GitHub Release 에 함께 올리면, updater endpoint 를 그 Release 의
#   latest.json URL 로 두는 것만으로 자동 업데이트가 동작한다.
# Dependencies: stdlib only
#
# 사용: python3 scripts/make_latest_json.py <version>
#   예) python3 scripts/make_latest_json.py 0.1.8
#
# 출력: build_output/latest.json
#
# url 필드는 GitHub Release 다운로드 URL 패턴으로 채운다(태그 v<version>).
# 리포는 GITHUB_REPOSITORY(CI) 또는 기본값(Intrect-io/vega-agent)을 사용.

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO_ROOT / "build_output"
UPDATER_DIR = BUILD_DIR / "updater"

# (Tauri platform key, 아티팩트 arch suffix)
PLATFORMS = [
    ("darwin-aarch64", "aarch64"),
    ("darwin-x86_64", "x86_64"),
]


def _release_url(repo: str, version: str, fname: str) -> str:
    return f"https://github.com/{repo}/releases/download/v{version}/{fname}"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: make_latest_json.py <version>", file=sys.stderr)
        return 2
    version = sys.argv[1].lstrip("v").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "Intrect-io/vega-agent")

    platforms: dict[str, dict] = {}
    for plat_key, arch in PLATFORMS:
        tgz = UPDATER_DIR / f"VEGA-{version}-{arch}.app.tar.gz"
        sig = UPDATER_DIR / f"VEGA-{version}-{arch}.app.tar.gz.sig"
        if not tgz.exists() or not sig.exists():
            print(f"[skip] {plat_key}: {tgz.name}(.sig) 없음", file=sys.stderr)
            continue
        platforms[plat_key] = {
            "signature": sig.read_text(encoding="utf-8").strip(),
            "url": _release_url(repo, version, tgz.name),
        }

    if not platforms:
        print("::error::updater 아티팩트가 하나도 없습니다 — latest.json 생성 불가", file=sys.stderr)
        return 1

    manifest = {
        "version": version,
        "notes": f"VEGA v{version}",
        # pub_date 는 빌드 시각이 아니라 ISO 문자열이면 되므로 CI 환경변수 우선,
        # 없으면 생략(Tauri 는 pub_date 없어도 동작).
        "platforms": platforms,
    }
    pub_date = os.environ.get("VEGA_PUB_DATE")
    if pub_date:
        manifest["pub_date"] = pub_date

    out = BUILD_DIR / "latest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {out} 생성 ({', '.join(platforms.keys())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
