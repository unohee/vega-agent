# Created: 2026-06-23
# Purpose: 모델 카탈로그 큐레이션 필터 회귀 (INT-1888 / EPIC INT-1876).
# Dependencies: pipeline.model_catalog
# Test Status: green (2026-06-23)

from __future__ import annotations

from pipeline.model_catalog import (
    curate_models,
    load_bench_scores,
    provider_allowed,
    provider_of,
    select_model_for_load,
    supports_caching,
    within_budget,
)


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
    assert "curated_reason" in out[0]


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
    assert supports_caching("qwen/qwen-3-max")  # Qwen 계열 caching 인정 (INT-1892)
    assert not supports_caching("meta-llama/llama-4")


# ── Provider 화이트리스트 (INT-1892) ──────────────────────────────────────────

def test_provider_of_and_allowed():
    assert provider_of("anthropic/claude-haiku") == "anthropic"
    assert provider_of("qwen/qwen-3-max") == "qwen"
    assert provider_of("bare-model") == ""
    for p in ("qwen", "deepseek", "google", "openai", "anthropic"):
        assert provider_allowed(f"{p}/whatever")
    assert not provider_allowed("meta-llama/llama-4")
    assert not provider_allowed("mistralai/mistral-large")
    assert not provider_allowed("x-ai/grok-4")


def test_qwen_now_curated():
    # Qwen 은 화이트리스트·caching 모두 통과 → 풀에 포함
    out = curate_models([_m("qwen/qwen-3-max", 0.3, 0.6)])
    assert [m["id"] for m in out] == ["qwen/qwen-3-max"]
    assert out[0]["curated"] is True


def test_provider_allowlist_excludes_non_allowed_even_if_cheap():
    # 저렴해도 화이트리스트 밖이면 제외 (provider 풀 제한)
    out = curate_models([
        _m("deepseek/deepseek-v4-flash", 0.1, 0.3),  # allowed
        _m("mistralai/mistral-small", 0.05, 0.1),     # not allowed
        _m("meta-llama/llama-4-scout", 0.05, 0.1),    # not allowed
        _m("x-ai/grok-4-mini", 0.05, 0.1),            # not allowed
    ])
    assert [m["id"] for m in out] == ["deepseek/deepseek-v4-flash"]


def test_curated_pool_only_allowed_providers():
    # 라우터(resolve_turn_model)가 고르는 풀 == curate_models 출력. 전부 allowed provider.
    out = curate_models([
        _m("qwen/qwen-3-max", 0.3, 0.6),
        _m("google/gemini-3.1-flash-lite", 0.1, 0.4),
        _m("openai/gpt-5.5-mini", 0.2, 0.6),
        _m("anthropic/claude-haiku-4-5", 0.5, 0.9),
        _m("deepseek/deepseek-v4-flash", 0.1, 0.3),
        _m("cohere/command-r", 0.1, 0.2),  # 제외 대상
    ])
    assert all(provider_allowed(m["id"]) for m in out)
    assert "cohere/command-r" not in [m["id"] for m in out]


def test_load_bench_scores_from_json(tmp_path):
    p = tmp_path / "bench.json"
    p.write_text('{"results":[{"model":"a/m","ratio":0.8},{"model":"a/m","ratio":1.0}]}', encoding="utf-8")
    assert load_bench_scores(p) == {"a/m": 0.9}


def test_select_model_for_load_uses_bench_scores():
    cat = [
        {"id": "a/cheap", "price_out_per_mtok": 0.2, "num_params_b": 7},
        {"id": "b/strong", "price_out_per_mtok": 0.9, "num_params_b": 400},
    ]
    scores = {"a/cheap": 0.95, "b/strong": 0.5}
    assert select_model_for_load("heavy", cat, bench_scores=scores)["id"] == "a/cheap"


def test_load_bench_scores_category_filter(tmp_path):
    p = tmp_path / "bench.json"
    p.write_text(
        '{"harness":"merged","results":['
        '{"model":"a/m","category":"office","harness":"agent","ratio":0.9},'
        '{"model":"a/m","category":"swe","harness":"smoke","ratio":0.4}'
        "]}",
        encoding="utf-8",
    )
    assert load_bench_scores(p, category="office", harness="agent") == {"a/m": 0.9}
    assert load_bench_scores(p, category="swe") == {"a/m": 0.4}
