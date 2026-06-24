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
_CACHING_PREFIXES = ("anthropic/", "openai/", "deepseek/", "google/")


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
    가격 초과 또는 caching 미지원 모델은 제외한다."""
    out = []
    for m in models:
        if not within_budget(m):
            continue
        if not supports_caching(m.get("id", "")):
            continue  # Prompt Caching 필수
        out.append({
            **m,
            "caching": True,
            "curated": True,
            "curated_reason": "≤$1/Mtok in+out, prompt caching required (INT-1888)",
        })
    out.sort(key=lambda m: (m.get("price_out_per_mtok") or 0.0))
    return out


def load_bench_scores(path: Path | str | None = None) -> dict[str, float]:
    """bench.json → model_id → mean rubric ratio (INT-1889 → INT-1892)."""
    p = Path(path) if path else DEFAULT_BENCH_PATH
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        by_model: dict[str, list[float]] = {}
        for row in data.get("results", []):
            if row.get("error") or "ratio" not in row:
                continue
            by_model.setdefault(row["model"], []).append(float(row["ratio"]))
        return {mid: sum(vals) / len(vals) for mid, vals in by_model.items() if vals}
    except Exception:
        return {}


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
                        m.get("price_out_per_mtok") or 0.0,
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
    by_price = sorted(curated, key=lambda m: (m.get("price_out_per_mtok") or 0.0))
    if load == "light":
        return by_price[0]
    if load == "heavy":
        return sorted(curated, key=lambda m: (m.get("num_params_b") or 0,
                                              m.get("price_out_per_mtok") or 0.0))[-1]
    return by_price[len(by_price) // 2]


def resolve_turn_model(load: str, bench_path: Path | str | None = None) -> str | None:
    """OpenRouter 활성 시 부하별 per-turn 모델 id (INT-1892). 실패 시 None."""
    try:
        from pipeline.llm_gateway import get_active_name

        if get_active_name() != "openrouter":
            return None
        from web.routers.llm import _fetch_models

        curated = curate_models(_fetch_models("openrouter"))
        scores = load_bench_scores(bench_path)
        picked = select_model_for_load(load, curated, bench_scores=scores or None)
        return picked.get("id") if picked else None
    except Exception:
        return None
