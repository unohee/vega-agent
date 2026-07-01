# Created: 2026-07-01
# Purpose: Regression for INT-2269 — flash is BACK in auto_route (the (d) streaming
#          safety net replaces the temporary exclusion), while the exclusion filter
#          infrastructure (_AUTO_ROUTE_EXCLUDED_MODELS + resolve_turn_model filter) is
#          kept for future degeneration-prone models (TECH #4322).

from __future__ import annotations

import pipeline.model_catalog as mc


_FLASH = {"id": "deepseek/deepseek-v4-flash", "price_in_per_mtok": 0.05, "price_out_per_mtok": 0.1}
_STABLE = {"id": "deepseek/deepseek-v3.2-exp", "price_in_per_mtok": 0.1, "price_out_per_mtok": 0.2}


def _wire(monkeypatch, models):
    import pipeline.llm_gateway as gw
    import web.routers.llm as wl
    monkeypatch.setattr(gw, "get_active_name", lambda: "openrouter")
    monkeypatch.setattr(gw, "get_active_provider", lambda: {"auto_route": True})
    monkeypatch.setattr(wl, "_fetch_models", lambda provider: list(models))
    monkeypatch.setattr(mc, "load_bench_scores", lambda *a, **k: {})  # heuristic (by-price) path


def test_light_picks_flash_when_cheapest():
    """flash is the cheapest, so unfiltered light routing picks it (safety net handles degen)."""
    curated = mc.curate_models([_FLASH, _STABLE])
    pick = mc.select_model_for_load("light", curated, {})
    assert pick["id"] == "deepseek/deepseek-v4-flash"


def test_exclusion_set_is_empty_flash_returned():
    """INT-2269 (d): exclusion set is now empty — flash returns to auto_route."""
    assert mc._AUTO_ROUTE_EXCLUDED_MODELS == set()


def test_resolve_turn_model_light_returns_flash(monkeypatch):
    """With flash no longer excluded, light auto_route resolves to the cheapest = flash."""
    _wire(monkeypatch, [_FLASH, _STABLE])
    assert mc.resolve_turn_model("light") == "deepseek/deepseek-v4-flash"


def test_exclusion_filter_infra_preserved(monkeypatch):
    """Filter infrastructure is kept: if a model is added to the set, it's excluded again."""
    monkeypatch.setattr(mc, "_AUTO_ROUTE_EXCLUDED_MODELS", {"deepseek/deepseek-v4-flash"})
    _wire(monkeypatch, [_FLASH, _STABLE])
    assert mc.resolve_turn_model("light") == "deepseek/deepseek-v3.2-exp"


def test_flash_still_in_curate_for_manual_ui():
    """UI manual selection pool (curate_models) keeps flash."""
    ids = {m["id"] for m in mc.curate_models([_FLASH, _STABLE])}
    assert "deepseek/deepseek-v4-flash" in ids
