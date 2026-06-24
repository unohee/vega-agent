# Created: 2026-06-23
# Purpose: 업무 부하 분류·라운드 상한·부하별 모델 선택 회귀 (INT-1892/1893).
# Dependencies: pipeline.tier_router, pipeline.model_catalog
# Test Status: green (2026-06-23)

from __future__ import annotations

from pipeline.model_catalog import select_model_for_load
from pipeline.tier_router import rounds_for_load, route_load, routing_text_from_messages


def test_route_load_heavy():
    assert route_load("이 파이썬 함수 디버그하고 리팩터해줘") == "heavy"
    assert route_load("매출 데이터 분석해서 상세히 설명하는 보고서 작성해줘") == "heavy"


def test_route_load_light_short_lookup():
    # 이케아 예시 — 짧은 단순 조회 (EPIC #1 이슈)
    assert route_load("이케아 5만원 이하 사무용 조명 5개 추천해줘") == "light"
    assert route_load("오늘 환율 얼마야?") == "light"
    assert route_load("이 파일 분석해줘") == "light"  # bare 분석해 — heavy 아님 (INT-1893)


def test_routing_text_from_messages_ignores_history():
    msgs = [
        {"role": "user", "content": "매출 데이터 분석해서 보고서 작성해줘"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "이케아 5만원 이하 사무용 조명 5개 추천해줘"},
    ]
    assert routing_text_from_messages(msgs) == msgs[-1]["content"]
    assert route_load(routing_text_from_messages(msgs)) == "light"
    assert rounds_for_load(routing_text_from_messages(msgs)) == 10


def test_route_load_standard_default():
    long_mixed = "이번 분기 작품 정산 내역을 봐주고 누락된 게 있으면 알려주고 다음 분기 계획도 같이 잡아줘 " * 2
    assert route_load(long_mixed) == "standard"


def test_rounds_for_load_caps():
    assert rounds_for_load("이케아 조명 추천") == 10          # light
    assert rounds_for_load("리팩터해줘") == 24                 # heavy
    assert rounds_for_load("x" * 200) == 20                    # standard(긴 비-heavy)
    assert rounds_for_load("뭐든", research_mode=True) == 40   # research 우선


def _m(mid, pout, params=None):
    return {"id": mid, "price_out_per_mtok": pout, "num_params_b": params}


def test_select_model_for_load():
    cat = [_m("a/cheap", 0.2, 7), _m("b/mid", 0.5, 70), _m("c/strong", 0.9, 400)]
    assert select_model_for_load("light", cat)["id"] == "a/cheap"      # 최저가
    assert select_model_for_load("heavy", cat)["id"] == "c/strong"     # 최대 params
    assert select_model_for_load("standard", cat)["id"] == "b/mid"     # 중앙값
    assert select_model_for_load("light", []) is None
