# Created: 2026-07-01
# Purpose: Regression for INT-2269 — auto_route must exclude the degeneration-prone
#          deepseek-v4-flash from automatic model selection (TECH #4322/#4294).

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


def test_light_would_pick_flash_without_exclusion():
    """Sanity: flash is the cheapest, so unfiltered light routing WOULD pick it."""
    curated = mc.curate_models([_FLASH, _STABLE])
    pick = mc.select_model_for_load("light", curated, {})
    assert pick["id"] == "deepseek/deepseek-v4-flash"


def test_resolve_turn_model_light_excludes_flash(monkeypatch):
    _wire(monkeypatch, [_FLASH, _STABLE])
    assert mc.resolve_turn_model("light") == "deepseek/deepseek-v3.2-exp"


def test_resolve_turn_model_no_flash_leak_any_load(monkeypatch):
    _wire(monkeypatch, [_FLASH, _STABLE])
    for load in ("light", "standard", "heavy"):
        assert mc.resolve_turn_model(load) != "deepseek/deepseek-v4-flash"


def test_flash_still_in_curate_for_manual_ui():
    """UI manual selection pool (curate_models) keeps flash — only auto_route excludes it."""
    ids = {m["id"] for m in mc.curate_models([_FLASH, _STABLE])}
    assert "deepseek/deepseek-v4-flash" in ids
    assert "deepseek/deepseek-v4-flash" in mc._AUTO_ROUTE_EXCLUDED_MODELS
