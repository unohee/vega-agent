#!/usr/bin/env python3
# Created: 2026-07-02
# Purpose: Single source of truth for bumping the VEGA desktop version across all
#   three files that must stay in sync: desktop/tauri.conf.json, desktop/Cargo.toml,
#   desktop/Cargo.lock (the vega-desktop package entry). Manual 3-file bumps kept
#   drifting / getting missed on merge (PR #90, #94) — this makes it atomic.
# Usage:
#   python scripts/bump_version.py patch|minor|major   → bump and print new version
#   python scripts/bump_version.py --current           → print current version only
#   python scripts/bump_version.py --set 0.1.55        → set an explicit version
# Dependencies: stdlib only.

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "desktop" / "tauri.conf.json"
CARGO = ROOT / "desktop" / "Cargo.toml"
LOCK = ROOT / "desktop" / "Cargo.lock"


def current() -> str:
    return json.loads(TAURI.read_text(encoding="utf-8"))["version"]


def _bump(ver: str, part: str) -> str:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", ver)
    if not m:
        raise SystemExit(f"현재 버전이 x.y.z 형식이 아님: {ver!r}")
    major, minor, patch = (int(x) for x in m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"알 수 없는 bump part: {part!r} (patch|minor|major)")


def _write_tauri(old: str, new: str) -> None:
    # Preserve formatting: replace only the version string value.
    text = TAURI.read_text(encoding="utf-8")
    new_text = re.sub(r'("version"\s*:\s*)"' + re.escape(old) + '"', r'\1"' + new + '"', text, count=1)
    if new_text == text:
        raise SystemExit(f"tauri.conf.json 에서 version {old} 를 못 찾음")
    TAURI.write_text(new_text, encoding="utf-8")


def _write_cargo(old: str, new: str) -> None:
    text = CARGO.read_text(encoding="utf-8")
    # Only the first top-level version = "..." (package version), not deps.
    new_text = re.sub(r'(?m)^(version\s*=\s*)"' + re.escape(old) + '"', r'\1"' + new + '"', text, count=1)
    if new_text == text:
        raise SystemExit(f"Cargo.toml 에서 version {old} 를 못 찾음")
    CARGO.write_text(new_text, encoding="utf-8")


def _write_lock(old: str, new: str) -> None:
    text = LOCK.read_text(encoding="utf-8")
    # Bump only the vega-desktop package entry: name line followed by version line.
    pat = re.compile(r'(name = "vega-desktop"\nversion = )"' + re.escape(old) + '"')
    new_text, n = pat.subn(r'\1"' + new + '"', text, count=1)
    if n == 0:
        raise SystemExit(f"Cargo.lock 에서 vega-desktop version {old} 를 못 찾음")
    LOCK.write_text(new_text, encoding="utf-8")


def set_version(new: str) -> str:
    old = current()
    if old == new:
        raise SystemExit(f"버전이 이미 {new} 임")
    _write_tauri(old, new)
    _write_cargo(old, new)
    _write_lock(old, new)
    return new


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("usage: bump_version.py patch|minor|major | --current | --set X.Y.Z")
    arg = argv[0]
    if arg == "--current":
        print(current())
        return
    if arg == "--set":
        if len(argv) < 2:
            raise SystemExit("--set 다음에 버전을 지정하세요 (예: --set 0.1.55)")
        print(set_version(argv[1]))
        return
    new = _bump(current(), arg)
    set_version(new)
    print(new)


if __name__ == "__main__":
    main(sys.argv[1:])
