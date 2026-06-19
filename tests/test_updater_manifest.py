# Created: 2026-06-10
# Purpose: updater 매니페스트(make_latest_json) + endpoints 정합성 회귀 테스트 (INT-1432)
#          private 리포라 R2(download.intrect.io) URL 이어야 하고, latest.json url 과
#          tauri.conf endpoints 가 같은 공개 prefix 를 가리켜야 한다.
# Dependencies: scripts/make_latest_json.py, desktop/tauri.conf.json
# Test Status: green (2026-06-10)

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_mlj():
    spec = importlib.util.spec_from_file_location("mlj", REPO / "scripts" / "make_latest_json.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["mlj"] = m
    spec.loader.exec_module(m)
    return m


def test_r2_url_shape():
    m = _load_mlj()
    url = m._r2_url("https://download.intrect.io/vega/updates", "0.1.10",
                    "VEGA-0.1.10-aarch64.app.tar.gz")
    assert url == "https://download.intrect.io/vega/updates/v0.1.10/VEGA-0.1.10-aarch64.app.tar.gz"


def test_default_base_is_public_r2_not_github():
    """private 리포 → updater 익명 GET 은 GitHub Release 에 접근 불가.
    기본 URL 이 github.com 이면 배포본 업데이트가 깨진다 (회귀 방지)."""
    m = _load_mlj()
    assert "github.com" not in m.DEFAULT_UPDATE_BASE
    assert m.DEFAULT_UPDATE_BASE.startswith("https://download.intrect.io/")


def test_manifest_url_matches_endpoint_prefix():
    """latest.json 의 자산 url 과 tauri.conf endpoints 가 같은 공개 prefix 아래여야
    한 곳(R2)만 올리면 updater 가 자산까지 도달한다."""
    m = _load_mlj()
    conf = json.loads((REPO / "desktop" / "tauri.conf.json").read_text(encoding="utf-8"))
    endpoint = conf["plugins"]["updater"]["endpoints"][0]
    assert endpoint == "https://download.intrect.io/vega/updates/latest.json"
    asset = m._r2_url(m.DEFAULT_UPDATE_BASE, "0.1.10", "VEGA-0.1.10-x86_64.app.tar.gz")
    assert asset.rsplit("/v0.1.10/", 1)[0] == endpoint.rsplit("/latest.json", 1)[0]


def test_no_placeholder_left_in_endpoints():
    """placeholder 가 남아있으면 업데이트가 조용히 죽는다 — 명시적으로 막는다."""
    conf = json.loads((REPO / "desktop" / "tauri.conf.json").read_text(encoding="utf-8"))
    for ep in conf["plugins"]["updater"]["endpoints"]:
        assert "PLACEHOLDER" not in ep and "example.com" not in ep


def test_version_sync():
    """tauri.conf.json 과 Cargo.toml 버전 일치 — 어긋나면 산출물 이름이 꼬인다."""
    conf_ver = json.loads((REPO / "desktop" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    cargo = (REPO / "desktop" / "Cargo.toml").read_text(encoding="utf-8")
    cargo_ver = next(
        l.split('"')[1] for l in cargo.splitlines() if l.startswith("version = ")
    )
    assert conf_ver == cargo_ver, f"tauri.conf={conf_ver} != Cargo={cargo_ver}"


# ── verify_update_manifest.py 로직 (배포 후 CI 검증 스크립트) ──────────────────

def _load_verify():
    spec = importlib.util.spec_from_file_location(
        "vum", REPO / "scripts" / "verify_update_manifest.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["vum"] = m
    spec.loader.exec_module(m)
    return m


def test_verify_passes_on_good_manifest(monkeypatch):
    v = _load_verify()

    def fake_get(url, timeout=20.0, retries=3, head=False):
        if url.endswith("latest.json"):
            return 200, json.dumps({
                "version": "0.1.40",
                "platforms": {
                    "darwin-aarch64": {"url": "https://x/v0.1.40/a.tar.gz", "signature": "sig"},
                },
            }).encode()
        return 200, None  # 자산 HEAD

    monkeypatch.setattr(v, "_get", fake_get)
    errs = v.verify("0.1.40", ["darwin-aarch64"], v.DEFAULT_BASE, True)
    assert errs == [], errs


def test_verify_detects_missing_sig_bad_url_and_missing_platform(monkeypatch):
    v = _load_verify()

    def fake_get(url, timeout=20.0, retries=3, head=False):
        if url.endswith("latest.json"):
            return 200, json.dumps({
                "version": "0.1.39",  # 기대(0.1.40)와 불일치
                "platforms": {
                    # 빈 서명 + 구버전 url, darwin-x86_64 누락
                    "darwin-aarch64": {"url": "https://x/v0.1.39/a.tar.gz", "signature": ""},
                },
            }).encode()
        return 200, None

    monkeypatch.setattr(v, "_get", fake_get)
    errs = v.verify("0.1.40", ["darwin-aarch64", "darwin-x86_64"], v.DEFAULT_BASE, True)
    joined = " ".join(errs)
    assert "version" in joined          # top-level 버전 불일치
    assert "signature" in joined        # 빈 서명
    assert "v0.1.40" in joined          # 자산 url 버전 불일치
    assert "누락" in joined             # darwin-x86_64 누락


def test_verify_flags_non_200_asset(monkeypatch):
    v = _load_verify()

    def fake_get(url, timeout=20.0, retries=3, head=False):
        if url.endswith("latest.json"):
            return 200, json.dumps({
                "version": "0.1.40",
                "platforms": {"darwin-aarch64": {"url": "https://x/v0.1.40/a.tar.gz", "signature": "sig"}},
            }).encode()
        return 404, None  # 자산 HTTP 404

    monkeypatch.setattr(v, "_get", fake_get)
    errs = v.verify("0.1.40", ["darwin-aarch64"], v.DEFAULT_BASE, True)
    assert any("404" in e for e in errs), errs
