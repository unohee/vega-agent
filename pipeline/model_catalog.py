# Created: 2026-06-23
# Purpose: 모델 카탈로그 큐레이션 — OpenRouter 전체 노출을 선정 기준으로 좁힌다 (INT-1888 / EPIC INT-1876)
# Dependencies: web.routers.llm._fetch_models 가 주는 정규화 모델 dict (price_in/out_per_mtok 등)

"""사무 업무용 모델 큐레이션.

INT-1876 PLAN 선정 기준:
- 가격: in·out 모두 ≤ $1 / Mtok (하네스가 토큰을 많이 쓰므로 저비용 필수)
- Prompt Caching 지원 (반복 시스템 프롬프트 캐싱으로 실비용 절감 — 필수)
- Non-frontier / ZDR 가능 (프런티어 플래그십 제외)

OpenRouter /models 는 가격은 주지만 caching·ZDR 메타데이터는 주지 않는다:
- caching: OpenRouter 경유 prompt caching 을 지원하는 프로바이더 계열(id prefix)로 판정.
- ZDR: 계정 단위 data-policy 설정이라 모델별 API 판정 불가 → 가격(≤$1)으로 non-frontier 를
  근사하고, 실제 ZDR 보장은 사용자의 OpenRouter privacy 설정에 위임(문서 안내).
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_PRICE_PER_MTOK = 1.0
DEFAULT_BENCH_PATH = Path(__file__).resolve().parent.parent / "build_output" / "bench.json"

# OpenRouter 경유 prompt caching 을 지원하는 프로바이더 계열 (모델 id prefix, 2026-06 기준).
_CACHING_PREFIXES = ("anthropic/", "openai/", "deepseek/", "google/", "qwen/")

# 선택 가능 provider 화이트리스트 (운영자 결정 — 벤치 통과·품질·caching 검증된 계열만).
# OpenRouter 전체 노출 대신 이 5개 계열로 고정한다 (EPIC INT-1876 / INT-1892).
_ALLOWED_PROVIDERS = ("qwen", "deepseek", "google", "openai", "anthropic")

# auto_route 자동 선택에서 제외할 degeneration 상습 모델 (INT-2269, TECH #4322/#4294).
# deepseek-v4-flash 가 최저가라 light 자동 라우팅에 뽑히는데, 최종 답변 라운드에서
# raw vocab spew(외계어)로 붕괴하는 사례가 반복됐다. UI 수동 선택은 여전히 허용하고
# (사용자 책임) auto_route 만 안정 모델을 고르도록 자동 후보에서만 뺀다.
_AUTO_ROUTE_EXCLUDED_MODELS = {"deepseek/deepseek-v4-flash"}

# 이 계열은 가격·caching 게이트 없이 카탈로그 전체를 노출한다 (INT-2002 재개방).
# 사용자가 플래그십(claude-opus, gpt-5.x, gemini-pro 등 $1 초과 모델)을 직접 고를 수 있게 한다.
# 단 가격 미상(None) 모델이 auto-route 최저가 픽을 오염시키지 않도록 정렬에서 맨 뒤로 보낸다
# (_price_sort_key — INT-2002/INT-1999).
_ALWAYS_OPEN_PROVIDERS = ("google", "openai", "anthropic")


def _price_sort_key(m: dict) -> float:
    """out 가격 오름차순 정렬 키. 가격 미상(None)은 맨 뒤로(최저가 오인 방지)."""
    po = m.get("price_out_per_mtok")
    return po if po is not None else float("inf")


def provider_of(model_id: str) -> str:
    """OpenRouter 모델 id 의 provider 계열 prefix (예: 'anthropic/claude-...' → 'anthropic')."""
    mid = (model_id or "").lower()
    return mid.split("/", 1)[0] if "/" in mid else ""


def provider_allowed(model_id: str) -> bool:
    return provider_of(model_id) in _ALLOWED_PROVIDERS


def supports_caching(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return any(mid.startswith(p) for p in _CACHING_PREFIXES)


def within_budget(m: dict) -> bool:
    """in·out 모두 ≤ $1/Mtok. 가격 미상(None)은 예산 보장 불가라 보수적으로 제외."""
    pi = m.get("price_in_per_mtok")
    po = m.get("price_out_per_mtok")
    if pi is None or po is None:
        return False
    return pi <= MAX_PRICE_PER_MTOK and po <= MAX_PRICE_PER_MTOK


def curate_models(models: list[dict]) -> list[dict]:
    """정규화된 모델 목록(_fetch_models 결과)을 선정 기준으로 좁힌다.

    통과 모델에 caching=True·curated=True 를 달고 (out 가격 오름차순) 정렬해 반환.
    화이트리스트 외 provider·가격 초과·caching 미지원 모델은 제외한다."""
    out = []
    for m in models:
        mid = m.get("id", "")
        if not provider_allowed(mid):
            continue  # Qwen/Deepseek/Google/OpenAI/Anthropic 계열만 노출 (INT-1892)
        always_open = provider_of(mid) in _ALWAYS_OPEN_PROVIDERS
        if not always_open:
            if not within_budget(m):
                continue
            if not supports_caching(mid):
                continue  # Prompt Caching 필수
        out.append({
            **m,
            "caching": supports_caching(mid),
            "curated": True,
            "curated_reason": (
                "google/openai/anthropic full catalog (INT-2002)"
                if always_open and not within_budget(m)
                else "≤$1/Mtok in+out, prompt caching, allowed provider (INT-1888/1892)"
            ),
        })
    out.sort(key=_price_sort_key)
    return out


def load_bench_scores(
    path: Path | str | None = None,
    *,
    category: str | None = None,
    harness: str | None = None,
    source: str | None = None,
    routing_only: bool = False,
) -> dict[str, float]:
    """bench.json → model_id → mean rubric ratio (INT-1889 → INT-1892).

    category: office|swe|multilingual|creative — 해당 카테고리 태스크만 집계.
    harness: smoke|agent — 지정 시 해당 harness 결과만 (없으면 전체).
    source: external suite name (humaneval, mbpp, ...) — 지정 시 해당 source만.
    routing_only: True면 task id ext_* 또는 native만 (summary_by_source native 제외 옵션).
    """
    p = Path(path) if path else DEFAULT_BENCH_PATH
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        artifact_harness = data.get("harness")
        by_model: dict[str, list[float]] = {}
        for row in data.get("results", []):
            if row.get("error") or "ratio" not in row:
                continue
            if category and row.get("category") != category:
                continue
            rh = row.get("harness") or artifact_harness
            if harness and rh and rh != harness:
                continue
            row_source = row.get("source") or row.get("suite")
            if source and row_source != source:
                continue
            if routing_only and row_source and source is None:
                pass  # include all when routing_only without source filter
            by_model.setdefault(row["model"], []).append(float(row["ratio"]))
        return {mid: sum(vals) / len(vals) for mid, vals in by_model.items() if vals}
    except Exception:
        return {}


_SWE_SOURCES = frozenset({"humaneval", "mbpp", "swebench_lite"})
_OFFICE_SOURCES = frozenset({
    "presentbench", "slidesgen", "deckbench", "odysseybench", "officeeval", "adbench",
})


def select_model_for_load(
    load: str,
    curated: list[dict],
    bench_scores: dict[str, float] | None = None,
) -> dict | None:
    """업무 부하별 모델 선택 — 큐레이션 카탈로그에서 고른다 (INT-1892 1차 휴리스틱).

    벤치 점수(bench.json)가 있으면 ratio 기반 정렬, 없으면 가격·params 휴리스틱.
    OpenRouter per-turn 오버라이드: resolve_turn_model() → build_request(model_override=...).
    """
    if not curated:
        return None
    if bench_scores:
        scored = [m for m in curated if bench_scores.get(m.get("id", "")) is not None]
        if scored:
            if load == "light":
                return min(
                    scored,
                    key=lambda m: (
                        _price_sort_key(m),
                        -bench_scores[m["id"]],
                    ),
                )
            if load == "heavy":
                return max(
                    scored,
                    key=lambda m: (
                        bench_scores[m["id"]],
                        m.get("num_params_b") or 0,
                        m.get("price_out_per_mtok") or 0.0,
                    ),
                )
            ranked = sorted(scored, key=lambda m: bench_scores[m["id"]])
            return ranked[len(ranked) // 2]
    by_price = sorted(curated, key=_price_sort_key)
    if load == "light":
        return by_price[0]
    if load == "heavy":
        return sorted(curated, key=lambda m: (m.get("num_params_b") or 0,
                                              m.get("price_out_per_mtok") or 0.0))[-1]
    return by_price[len(by_price) // 2]


def resolve_turn_model(load: str, bench_path: Path | str | None = None) -> str | None:
    """OpenRouter 활성 + auto_route 켜짐일 때 부하별 per-turn 모델 id (INT-1892). 아니면 None.

    수동 선택 우선: 사용자가 특정 모델을 고르면 auto_route=False 가 되어 None 을 반환,
    build_request 가 default_model(수동 선택)을 그대로 쓴다. 자동 라우팅은 "자동" 선택 시에만."""
    try:
        from pipeline.llm_gateway import get_active_name, get_active_provider

        if get_active_name() != "openrouter":
            return None
        if not bool(get_active_provider().get("auto_route")):
            return None  # 수동 선택 모델 우선 — 라우터 비활성
        from web.routers.llm import _fetch_models

        curated = [
            m for m in curate_models(_fetch_models("openrouter"))
            if m.get("id") not in _AUTO_ROUTE_EXCLUDED_MODELS
        ]
        import os
        bench_path = bench_path or os.getenv("VEGA_BENCH_PATH") or DEFAULT_BENCH_PATH
        if load == "standard":
            scores = load_bench_scores(bench_path, category="office", harness="agent")
            if not scores:
                scores = load_bench_scores(bench_path, category="office")
        elif load == "heavy":
            scores = load_bench_scores(bench_path, category="swe", harness="agent")
            if not scores:
                scores = load_bench_scores(bench_path, category="swe")
            if not scores:
                for src in _SWE_SOURCES:
                    part = load_bench_scores(bench_path, source=src, harness="smoke")
                    if part:
                        for k, v in part.items():
                            scores[k] = max(scores.get(k, 0), v)
        else:
            scores = load_bench_scores(bench_path, category="office", harness="agent")
            if not scores:
                scores = load_bench_scores(bench_path)
        picked = select_model_for_load(load, curated, bench_scores=scores or None)
        return picked.get("id") if picked else None
    except Exception:
        return None
