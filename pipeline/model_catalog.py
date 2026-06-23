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

MAX_PRICE_PER_MTOK = 1.0

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
        out.append({**m, "caching": True, "curated": True})
    out.sort(key=lambda m: (m.get("price_out_per_mtok") or 0.0))
    return out
