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


# ── VEGA_BUNDLE_ROOT 기반 번들 .env 픽업 (INT-1505) ───────────────────────────
# noarchive=False 인 PyInstaller 빌드에선 pipeline/keychain.py 가 PYZ 안에 압축돼
# __file__ 이 디스크의 _MEIPASS/pipeline/keychain.py 를 가리키지 않는다. 따라서
# __file__.parent.parent/.env 추정만으로는 배포 번들 .env(_MEIPASS/.env)를 놓쳐
# VEGA_SEARXNG_KEY 가 안 잡히고 search.intrect.io 가 401 이었다. launcher 가 명시
# 설정하는 VEGA_BUNDLE_ROOT env 로 번들 .env 를 확실히 픽업해야 한다.

def test_bundle_root_env_picks_up_bundle_dotenv(tmp_path, monkeypatch):
    """VEGA_BUNDLE_ROOT/.env 가 keychain 폴백 경로에 포함된다 (__file__ 추정과 독립)."""
    bundle = tmp_path / "meipass"
    bundle.mkdir()
    (bundle / ".env").write_text("VEGA_SEARXNG_KEY=bundle-searx-key\n", encoding="utf-8")
    data = tmp_path / "userdata"
    data.mkdir()
    monkeypatch.setattr(dp, "data_dir", lambda: data)
    monkeypatch.setattr(kc, "get_secret", lambda *a, **k: None)  # Keychain 비어있음
    # __file__ 은 디스크 어디에도 .env 가 없는 zip 내부 경로처럼 둔다(번들 .env 못 찾는 현실)
    monkeypatch.setattr(kc, "__file__", str(tmp_path / "nonexistent_zip" / "pipeline" / "keychain.py"))
    monkeypatch.setenv("VEGA_BUNDLE_ROOT", str(bundle))
    monkeypatch.delenv("VEGA_SEARXNG_KEY", raising=False)
    assert str(bundle / ".env") in [str(p) for p in kc._env_file_paths()]
    assert kc.get("VEGA_SEARXNG_KEY") == "bundle-searx-key"


def test_user_env_overrides_bundle_root(tmp_path, monkeypatch):
    """사용자 data_dir/.env 가 VEGA_BUNDLE_ROOT/.env 기본값을 덮는다 (우선순위 유지)."""
    bundle = tmp_path / "meipass"
    bundle.mkdir()
    (bundle / ".env").write_text("VEGA_SEARXNG_KEY=bundle-key\n", encoding="utf-8")
    data = tmp_path / "userdata"
    data.mkdir()
    (data / ".env").write_text("VEGA_SEARXNG_KEY=user-key\n", encoding="utf-8")
    monkeypatch.setattr(dp, "data_dir", lambda: data)
    monkeypatch.setattr(kc, "get_secret", lambda *a, **k: None)
    monkeypatch.setattr(kc, "__file__", str(tmp_path / "nonexistent_zip" / "pipeline" / "keychain.py"))
    monkeypatch.setenv("VEGA_BUNDLE_ROOT", str(bundle))
    monkeypatch.delenv("VEGA_SEARXNG_KEY", raising=False)
    assert kc.get("VEGA_SEARXNG_KEY") == "user-key"


# ── OAuth client 번들 회귀 (Google "OAuth client 없음" 버그) ──────────────────
# spec 에 slack 만 있고 google 이 빠져 frozen 앱에서 is_configured()=False 로
# "구성 안 됨"이 떴던 회귀를 막는다 (2026-06-10).

def test_spec_bundles_both_oauth_clients():
    from pathlib import Path
    spec = (Path(__file__).resolve().parent.parent / "bin" / "vega-backend.spec").read_text(encoding="utf-8")
    # slack 은 무조건 datas 에 명시
    assert "slack_oauth_client.json" in spec, "spec 에 slack OAuth client 번들 누락"
    # google 은 조건부(os.path.exists)지만 spec 에 반드시 등장해야 함
    assert "google_oauth_client.json" in spec, (
        "spec 에 google OAuth client 번들 누락 — frozen 앱에서 "
        "google.is_configured()=False → '구성 안 됨'으로 연결 불가"
    )


# ── 크로스플랫폼 토큰 저장 (Windows OAuth, INT-1494) ──────────────────────────
# Windows/Linux 에선 macOS `security` CLI 가 없어 OAuth 토큰 저장이 깨졌다.
# set/get/delete 가 비-macOS 에서 keyring 으로 위임되는지, auth 모듈이 중앙
# keychain 에 위임하는지 검증한다.

def test_non_macos_uses_keyring_backend(monkeypatch):
    """비-macOS(_HAS_KEYCHAIN=False)에서 set/get/delete 가 keyring 으로 라우팅된다."""
    store: dict[tuple[str, str], str] = {}

    class _FakeKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass

        def get_keyring(self):  # not fail backend
            return self

        def get_password(self, svc, key):
            return store.get((svc, key))

        def set_password(self, svc, key, val):
            store[(svc, key)] = val

        def delete_password(self, svc, key):
            if (svc, key) not in store:
                raise self.errors.PasswordDeleteError()
            del store[(svc, key)]

    monkeypatch.setattr(kc, "_HAS_KEYCHAIN", False)
    monkeypatch.setattr(kc, "_keyring", lambda: _FakeKeyring())

    assert kc.set_secret("refresh_token", "tok-abc", service="vega-google-oauth") is True
    assert kc.get_secret("refresh_token", service="vega-google-oauth") == "tok-abc"
    # 네임스페이스 격리: 같은 key, 다른 service 는 충돌하지 않는다
    assert kc.get_secret("refresh_token", service="vega-slack-oauth") is None
    assert kc.delete_secret("refresh_token", service="vega-google-oauth") is True
    assert kc.get_secret("refresh_token", service="vega-google-oauth") is None
    # 멱등 삭제 (이미 없음)
    assert kc.delete_secret("refresh_token", service="vega-google-oauth") is True


def test_non_macos_no_backend_returns_falsy(monkeypatch):
    """keyring 도 없으면(헤드리스) set=False·get=None — 예외 전파 없이 폴백."""
    monkeypatch.setattr(kc, "_HAS_KEYCHAIN", False)
    monkeypatch.setattr(kc, "_keyring", lambda: None)
    assert kc.set_secret("k", "v", service="s") is False
    assert kc.get_secret("k", service="s") is None
    assert kc.delete_secret("k", service="s") is False


def test_auth_modules_delegate_to_central_keychain():
    """google/slack/superthread 의 keychain_save/load 가 중앙 _kc 에 위임한다
    (각자 `security` 직접 호출하던 Windows-깨짐 패턴 제거 회귀)."""
    import inspect
    from pipeline.auth import google, slack, superthread
    for mod in (google, slack, superthread):
        src = inspect.getsource(mod.keychain_save) + inspect.getsource(mod.keychain_load)
        assert "_kc." in src, f"{mod.__name__}.keychain_* 가 중앙 keychain 에 위임하지 않음"
        assert "security" not in src, f"{mod.__name__} 가 여전히 `security` CLI 직접 호출"


def test_spec_bundles_keyring_windows_backend():
    """spec 이 keyring Windows 백엔드를 hiddenimport 로 못박는지 — 안 그러면 frozen
    Windows 앱에서 keyring 이 fail 백엔드로 떨어져 OAuth 토큰 저장이 또 깨진다."""
    from pathlib import Path
    spec = (Path(__file__).resolve().parent.parent / "bin" / "vega-backend.spec").read_text(encoding="utf-8")
    assert "keyring.backends.Windows" in spec, "spec 에 keyring Windows 백엔드 hiddenimport 누락 (INT-1494)"
