# Created: 2026-07-01
# Purpose: auth OAuth 보안 회귀 (INT-2233 audit) — state 검증·broker public-suffix·logout tombstone.

from __future__ import annotations


def test_google_exchange_rejects_missing_state():
    import pipeline.auth.google as g
    g._pending_state.clear()
    g._pending_state["state"] = "s1"
    r = g.exchange_code(code="c", state=None)  # state 누락 → 거부 (fail closed)
    assert r["ok"] is False
    assert "state" in (r.get("error") or "")


def test_google_exchange_rejects_mismatch_state():
    import pipeline.auth.google as g
    g._pending_state.clear()
    g._pending_state["state"] = "s1"
    r = g.exchange_code(code="c", state="wrong")
    assert r["ok"] is False


def test_google_exchange_rejects_when_no_pending():
    import pipeline.auth.google as g
    g._pending_state.clear()  # pending 없음 → 어떤 state 든 거부
    r = g.exchange_code(code="c", state="anything")
    assert r["ok"] is False


def test_broker_same_site_blocks_shared_public_suffix():
    from pipeline.auth.broker import _same_site
    # 같은 public suffix 의 다른 서브도메인은 same-site 가 아니어야 (자격 유출 차단)
    assert not _same_site("https://portal.github.io/a", "https://attacker.github.io/b")
    assert not _same_site("https://x.pages.dev", "https://y.pages.dev")
    assert not _same_site("https://a.workers.dev", "https://b.workers.dev")
    # 진짜 같은 등록가능 도메인은 same-site
    assert _same_site("https://a.example.com", "https://b.example.com")


def test_airtable_logout_blocks_env_fallback(monkeypatch):
    import pipeline.auth.airtable as a
    store: dict = {}
    monkeypatch.setattr(a._kc, "get_secret", lambda k, **kw: store.get(k))
    monkeypatch.setattr(a._kc, "set_secret", lambda k, v, **kw: (store.__setitem__(k, v), True)[1])
    monkeypatch.setattr(a._kc, "delete_secret", lambda k, **kw: (store.pop(k, None), True)[1])
    monkeypatch.setattr(a._kc, "get", lambda k: "env_pat")  # .env/env fallback 시뮬
    assert a.token() == "env_pat"      # logout 전: .env PAT 보임
    a.logout()
    assert a.token() is None           # logout 후: tombstone 이 .env fallback 무력화
    assert a.is_authenticated() is False


def test_github_logout_blocks_env_fallback(monkeypatch):
    import pipeline.auth.github as gh
    store: dict = {}
    monkeypatch.setattr(gh._kc, "get_secret", lambda k, **kw: store.get(k))
    monkeypatch.setattr(gh._kc, "set_secret", lambda k, v, **kw: (store.__setitem__(k, v), True)[1])
    monkeypatch.setattr(gh._kc, "delete_secret", lambda k, **kw: (store.pop(k, None), True)[1])
    monkeypatch.setattr(gh._kc, "get", lambda k: "env_pat")
    assert gh.token() == "env_pat"
    gh.logout()
    assert gh.token() is None
