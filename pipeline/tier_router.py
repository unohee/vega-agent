# Created: 2026-05-31
# Purpose: 2단 의도 라우터 — 사용자 요청을 "도메인 지식 질의/갱신"(local SLM) 또는
#          "즉각 업무지원"(cloud)으로 분류. 회사 업무 대부분은 결정론적 조회라 SLM 으로
#          충분하고, 문서 생성·웹 검색·추론 같은 생성 작업만 클라우드로 보낸다.
# Dependencies: stdlib only (휴리스틱). LLM 분류는 의도적으로 안 씀 — 라우팅에 LLM 호출은 배보다 배꼽.
"""요청 → tier 분류 (휴리스틱).

설계 근거 (운영자 결정):
- 회사 지식 관리 = 도메인 지식의 질의·갱신 (오늘 급한 일? 여유로운 사람?) → 결정론적,
  SLM 으로 충분. 로컬에서 비용 0, TTFT 낮음.
- 즉각 업무지원 (문서 작성, 웹 검색, 긴 추론) → 로컬 SLM 은 품질/TTFT 한계 → cloud.

판별은 휴리스틱 우선 (agent_loop 교훈: 토큰 길이보다 신호 단어가 견고, LLM 분류는 과함).
확신 없으면 cloud (안전한 기본값 — 품질 우선).
"""
from __future__ import annotations

import re

# 즉각 업무지원 신호 (→ cloud): 생성·추론·검색·번역·요약 등 SLM 이 약한 작업
_CLOUD_SIGNALS = [
    r"작성|초안|써\s*줘|써줘|적어\s*줘|만들어\s*줘|드래프트|draft|compose|write",
    r"이메일|메일\s*보내|답장|회신",
    r"요약|정리해|번역|translate|summari[sz]e",
    r"검색|찾아봐|알아봐|web\s*search|구글",
    r"분석해|추론|왜\s|이유를|설명해\s*줘.*길게|자세히\s*설명",
    r"코드|스크립트|구현|디버그|리팩터",
    r"비교해|장단점|제안해|추천해\s*줘",
]

# 도메인 지식 질의/갱신 신호 (→ local): 회사 데이터 조회·상태 확인 (결정론적)
_LOCAL_SIGNALS = [
    r"오늘|지금|이번\s*주|이번주|현재|최근",
    r"급한|우선순위|마감|deadline|급해",
    r"여유|바쁜|누가|담당|할당|assign",
    r"진행\s*중|상태|현황|status",
    r"작품|정산|로열티|OKR|카드|이슈|일정|미팅|회의",
    r"k\d+|komca|iswc",  # KYTE 식별자
    r"기억해|메모|업데이트해\s*줘.*상태",  # 지식 갱신
]


def _match_any(text: str, patterns: list[str]) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))


def route_tier(user_text: str, history: list[dict] | None = None) -> str:
    """요청을 "local"(도메인 질의/갱신) 또는 "cloud"(즉각 지원)로 분류.

    규칙:
    1. cloud 신호가 있으면 cloud (생성/추론은 품질 우선).
    2. cloud 신호 없고 local 신호만 있으면 local (결정론적 조회는 SLM).
    3. 둘 다 없거나 애매하면 cloud (안전한 기본값).
    """
    text = user_text or ""
    cloud_hits = _match_any(text, _CLOUD_SIGNALS)
    local_hits = _match_any(text, _LOCAL_SIGNALS)

    if cloud_hits > 0:
        return "cloud"
    if local_hits > 0:
        return "local"
    return "cloud"


# ── 업무 부하 분류 (INT-1892 라우팅 / INT-1893 추론·라운드 상한) ──────────────
# 단순 조회·보고(이케아 조명 추천 등)가 과도한 추론/툴 라운드를 쓰지 않게,
# 작업 부하를 light/standard/heavy 로 보수적 분류한다. 확신 없으면 standard.
_HEAVY_LOAD = [
    r"코드|스크립트|구현|디버그|리팩터|implement|refactor|debug",
    r"보고서|문서.*작성|장문|길게|자세히\s*설명|상세히|초안.*작성",
    # bare `분석해` 제외 — "이 파일 분석해줘"(≤80자)는 light (INT-1893)
    r"심층\s*분석|비교\s*분석|데이터\s*분석|코드\s*분석|분석해서|분석하고|분석\s*보고",
    r"설계|아키텍처|단계별|전체.*정리|마이그레이션",
]


def routing_text_from_messages(messages: list[dict]) -> str:
    """현재 턴 분류용 — preamble·히스토리 제외, 마지막 user 메시지만 (INT-1893)."""
    if not messages:
        return ""
    last = messages[-1]
    content = last.get("content") if isinstance(last, dict) else ""
    return content if isinstance(content, str) else ""


def route_load(user_text: str, history: list[dict] | None = None) -> str:
    """요청을 'light' | 'standard' | 'heavy' 로 분류 (부하 기준).

    - heavy: 코드·장문 생성·심층 분석/설계 신호 → 상위 모델·넉넉한 라운드.
    - light: 짧고(≤80자) heavy 신호 없는 단순 조회/검색/확인 → 저비용 모델·낮은 라운드 상한.
    - standard: 그 외 (안전한 기본값).
    """
    text = (user_text or "").strip()
    if _match_any(text, _HEAVY_LOAD):
        return "heavy"
    if len(text) <= 80:
        return "light"
    return "standard"


# 부하별 에이전트 툴 라운드 상한 (research_mode 는 호출부에서 40 으로 별도 처리).
_ROUNDS_BY_LOAD = {"light": 10, "standard": 20, "heavy": 24}
MAX_TOOL_ROUNDS_BY_LOAD = {"light": 2, "standard": 5, "heavy": 24}

# Load-aware API budget (INT-1893 Phase 1) — reasoning_effort None = provider default.
LOAD_BUDGET: dict[str, dict] = {
    "light": {"max_tokens": 1200, "reasoning_effort": "low"},
    "standard": {"max_tokens": 4000, "reasoning_effort": None},
    "heavy": {"max_tokens": 8000, "reasoning_effort": None},
}

_VALID_LOADS = frozenset({"light", "standard", "heavy"})


def rounds_for_load(user_text: str, research_mode: bool = False) -> int:
    """부하 분류 기반 에이전트 툴 루프 라운드 상한. 단순 작업의 과도한 반복을 막는다(INT-1893)."""
    if research_mode:
        return 40
    return _ROUNDS_BY_LOAD.get(route_load(user_text), 20)


def user_content_blob_from_messages(messages: list[dict], preamble: str = "") -> str:
    """stream_gpt 가 LLM 에 보내는 user_content 재구성 — before/after 측정·회귀용 (INT-1893)."""
    if not messages:
        return preamble.strip()
    if len(messages) == 1:
        user_content = messages[-1].get("content", "")
    else:
        turns = []
        for m in messages[:-1]:
            label = "User" if m.get("role") in ("user", "human") else "VEGA"
            turns.append(f"[{label}]: {m.get('content', '')}")
        history_block = "\n".join(turns)
        user_content = (
            f"[대화 히스토리]\n{history_block}\n\n[현재 메시지]\n{messages[-1].get('content', '')}"
        )
    if preamble.strip():
        user_content = f"{preamble.strip()}\n\n---\n\n{user_content}"
    return user_content


def classify_load_ambiguous(route_text: str, base_load: str) -> str:
    """80–200자 애매 구간 — optional local SLM (VEGA_LOAD_CLASSIFIER=1) or standard bump."""
    import os

    text = (route_text or "").strip()
    if base_load != "light" or len(text) <= 80 or len(text) > 200:
        return base_load
    if _match_any(text, _HEAVY_LOAD):
        return "heavy"
    if os.getenv("VEGA_LOAD_CLASSIFIER", "").strip() not in ("1", "true", "yes"):
        return base_load
    try:
        from pipeline.llm_gateway import get_provider_for_tier

        prov = get_provider_for_tier("local")
        if not prov or prov.get("auth_type") == "none" and "127.0.0.1" not in (prov.get("base_url") or ""):
            pass
        import json
        import urllib.request

        base_url = (prov.get("base_url") or "http://127.0.0.1:1234/v1").rstrip("/")
        model = prov.get("default_model") or "local"
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": "Reply with exactly one word: light, standard, or heavy."},
                {"role": "user", "content": f"Classify task load:\n{text[:400]}"},
            ],
            "max_tokens": 5,
            "temperature": 0,
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        raw = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        tag = raw.strip().lower().split()[0] if raw else ""
        if tag in _VALID_LOADS:
            return tag
    except Exception:
        pass
    return "standard"


def resolve_load_routing(
    messages: list[dict],
    *,
    research_mode: bool = False,
    load_override: str | None = None,
) -> dict:
    """현재 턴 부하·라운드 상한 — streaming telemetry 입력 (INT-1893)."""
    route_text = routing_text_from_messages(messages)
    if load_override in _VALID_LOADS:
        load = load_override
    else:
        load = route_load(route_text)
        load = classify_load_ambiguous(route_text, load)
    budget = LOAD_BUDGET.get(load, LOAD_BUDGET["standard"])
    max_rounds = 40 if research_mode else _ROUNDS_BY_LOAD.get(load, 20)
    return {
        "route_text": route_text,
        "load": load,
        "max_rounds": max_rounds,
        "max_tool_rounds": MAX_TOOL_ROUNDS_BY_LOAD.get(load, 5),
        "budget": dict(budget),
    }


def legacy_load_from_user_blob(messages: list[dict], preamble: str = "") -> str:
    """구버그 재현 — preamble+히스토리 전체 blob 으로 route_load (before 측정용)."""
    return route_load(user_content_blob_from_messages(messages, preamble=preamble))
