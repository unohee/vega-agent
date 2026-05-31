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
