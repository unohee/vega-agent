# Created: 2026-06-23
# Purpose: 모델 카탈로그 큐레이션 필터 회귀 (INT-1888 / EPIC INT-1876).
# Dependencies: pipeline.model_catalog
# Test Status: green (2026-06-23)

from __future__ import annotations

from pipeline.model_catalog import curate_models, supports_caching, within_budget


def _m(mid, pin, pout):
    return {"id": mid, "name": mid, "price_in_per_mtok": pin, "price_out_per_mtok": pout}


def test_filters_out_over_budget():
    out = curate_models([
        _m("deepseek/deepseek-v4-flash", 0.1, 0.3),
        _m("anthropic/claude-opus-4-8", 15.0, 75.0),  # 프런티어 — $1 초과
    ])
    ids = [m["id"] for m in out]
    assert "deepseek/deepseek-v4-flash" in ids
    assert "anthropic/claude-opus-4-8" not in ids


def test_caching_required():
    # 가격은 통과하지만 caching 미지원 프로바이더 → 제외
    assert curate_models([_m("some-vendor/cheap", 0.1, 0.2)]) == []


def test_caching_capable_within_budget_passes_with_flags():
    out = curate_models([_m("openai/gpt-5.5-mini", 0.2, 0.6)])
    assert len(out) == 1
    assert out[0]["caching"] is True and out[0]["curated"] is True


def test_unknown_price_excluded():
    # 가격 미상은 예산 보장 불가 → 보수적 제외
    assert curate_models([{"id": "deepseek/x", "price_in_per_mtok": None,
                           "price_out_per_mtok": None}]) == []


def test_boundary_exactly_one_dollar_included():
    assert within_budget(_m("google/gemini", 1.0, 1.0)) is True
    assert within_budget(_m("google/gemini", 1.0, 1.01)) is False


def test_sorted_by_out_price_ascending():
    out = curate_models([_m("deepseek/a", 0.1, 0.9), _m("google/b", 0.1, 0.2)])
    assert [m["id"] for m in out] == ["google/b", "deepseek/a"]


def test_supports_caching_prefixes():
    assert supports_caching("anthropic/claude-haiku")
    assert supports_caching("google/gemini-3.1-flash-lite")
    assert not supports_caching("meta-llama/llama-4")
