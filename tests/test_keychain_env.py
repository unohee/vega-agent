# Created: 2026-06-10
# Purpose: keychain .env 폴백 체인 테스트 — 배포 번들 .env(_MEIPASS/.env) 픽업 (INT-1430 후속)
# Dependencies: pipeline/keychain.py, pipeline/data_paths.py
# Test Status: green (2026-06-10)

from __future__ import annotations

import pytest

import pipeline.data_paths as dp
import pipeline.keychain as kc


@pytest.fixture(autouse=True)
def _reset_env_cache():
    kc._ENV_CACHE = None
    yield
    kc._ENV_CACHE = None


def _frozen_layout(tmp_path, monkeypatch, bundle_key: str | None):
    """frozen 앱 레이아웃 시뮬레이션 — keychain.__file__이 _MEIPASS/pipeline/ 아래,
    번들 루트(_MEIPASS/.env)에 배포 기본 키. build_dmg.sh [pre] + spec 번들 결과 모사."""
    bundle = tmp_path / "meipass"
    (bundle / "pipeline").mkdir(parents=True)
    if bundle_key is not None:
        (bundle / ".env").write_text(f"VEGA_API_KEY={bundle_key}\n", encoding="utf-8")
    monkeypatch.setattr(kc, "__file__", str(bundle / "pipeline" / "keychain.py"))
    data = tmp_path / "userdata"
    data.mkdir()
    monkeypatch.setattr(dp, "data_dir", lambda: data)
    monkeypatch.setattr(kc, "get_secret", lambda *a, **k: None)  # Keychain 비어있음
    return data


def test_bundle_env_default_key_found(tmp_path, monkeypatch):
    """신규 설치 사용자: Keychain·사용자 .env 없음 → 번들 기본 키가 잡힌다."""
    _frozen_layout(tmp_path, monkeypatch, bundle_key="bundle-key")
    monkeypatch.delenv("VEGA_API_KEY", raising=False)
    assert kc.get("VEGA_API_KEY") == "bundle-key"


def test_user_env_overrides_bundle(tmp_path, monkeypatch):
    """사용자 데이터 디렉터리 .env가 번들 기본값을 덮는다 (앞 경로 우선 병합)."""
    data = _frozen_layout(tmp_path, monkeypatch, bundle_key="bundle-key")
    (data / ".env").write_text("VEGA_API_KEY=user-key\n", encoding="utf-8")
    assert kc.get("VEGA_API_KEY") == "user-key"


def test_keychain_overrides_all(tmp_path, monkeypatch):
    """Keychain 값이 .env들보다 우선."""
    _frozen_layout(tmp_path, monkeypatch, bundle_key="bundle-key")
    monkeypatch.setattr(kc, "get_secret", lambda key, **k: "keychain-key" if key == "VEGA_API_KEY" else None)
    assert kc.get("VEGA_API_KEY") == "keychain-key"


def test_no_bundle_env_falls_to_default(tmp_path, monkeypatch):
    """번들 .env조차 없으면(개발 spec 수동 빌드) default 반환 — 크래시 없음."""
    _frozen_layout(tmp_path, monkeypatch, bundle_key=None)
    monkeypatch.delenv("VEGA_API_KEY", raising=False)
    assert kc.get("VEGA_API_KEY", default="") == ""


# ── OAuth client 번들 회귀 (Google "OAuth client 없음" 버그) ──────────────────
# spec 에 slack 만 있고 google 이 빠져 frozen 앱에서 is_configured()=False 로
# "구성 안 됨"이 떴던 회귀를 막는다 (2026-06-10).

def test_spec_bundles_both_oauth_clients():
    from pathlib import Path
    spec = (Path(__file__).resolve().parent.parent / "bin" / "vega-backend.spec").read_text()
    # slack 은 무조건 datas 에 명시
    assert "slack_oauth_client.json" in spec, "spec 에 slack OAuth client 번들 누락"
    # google 은 조건부(os.path.exists)지만 spec 에 반드시 등장해야 함
    assert "google_oauth_client.json" in spec, (
        "spec 에 google OAuth client 번들 누락 — frozen 앱에서 "
        "google.is_configured()=False → '구성 안 됨'으로 연결 불가"
    )
