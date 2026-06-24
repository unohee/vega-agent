# INT-1893 — 이케아 조명 E2E 시나리오

Epic [INT-1876](https://linear.app/intrect/issue/INT-1876) / [INT-1893](https://linear.app/intrect/issue/INT-1893) 검증용.

## 재현 시나리오

1. 세션에서 먼저 heavy 턴을 보낸다:  
   `매출 데이터 분석해서 보고서 작성해줘`
2. 모델이 응답한 뒤 light 턴:  
   `이케아 5만원 이하 사무용 조명 5개 추천해줘`
3. 기대 동작 (after fix):
   - `load=light`, `max_rounds=10`
   - `web_search` 1~2회 이내로 답변 종료 (과도한 추론·툴 라운드 없음)

## Before (버그)

- `stream_gpt` 가 preamble+히스토리 전체 `user_content` 로 `route_load` → **heavy**
- `max_rounds=24` → 단순 조회에도 긴 tool loop 허용

## After (fix)

- `routing_text_from_messages()` — **마지막 user 메시지만** 분류
- `_HEAVY_LOAD` 에서 bare `분석해` 제거 → 짧은 "이 파일 분석해줘" 는 light

## 측정

```bash
python scripts/measure_load_rounds.py
# → build_output/int1893_before_after.json
```

세션 stats (streaming): `load`, `max_rounds`, `actual_rounds`, `tool_rounds`, `output_tokens`.

## 회귀 테스트

- `tests/test_int1893_overthinking.py`
- `tests/test_routing.py`
- `tests/test_streaming.py` (integration mock)
