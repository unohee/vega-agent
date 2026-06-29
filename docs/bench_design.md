# Bench Design (INT-1876)

Epic [INT-1876](https://linear.app/intrect/issue/INT-1876) — 업무별 모델 벤치마크 설계.

**Results (local):** [bench_results.md](./bench_results.md) · **External exclusions:** [bench_external_exclusions.md](./bench_external_exclusions.md)

## Dual Harness

| Harness | Script | 경로 | 용도 |
|---------|--------|------|------|
| **Smoke** | `scripts/bench_models.py` | OpenRouter 단일 completion | CI·저비용 회귀, Judge + verify |
| **Agent** | `scripts/bench_agent.py` | `stream_gpt` E2E + 도구 | 라우팅 ground truth (office) |

공통 라이브러리: `scripts/bench_lib.py`

## 태스크 (27개)

정의: `data/bench_tasks.json` — `harness`: smoke|agent|both, `verify`: office|swe|none, agent 태스크는 `required_tools`/`forbidden_tools`/`min_tool_rounds`로 도구 호출 채점.

- **office (18)**: press_release, excel_calc, excel_create_e2e, excel_read_fix, **excel_read_fix_save**, **python_calc_xlsx**, **web_search_summarize**, pdf_summarize, biz_email_reply, meeting_minutes_struct, slide_outline_json, slide_deck_create, proposal_rfp, **proposal_rfp_agent**, proposal_json, ad_copy_campaign, **ad_copy_research**, ad_copy_json
- **swe (5)**: py_bugfix, py_implement, py_is_palindrome, py_fizzbuzz, py_clamp
- **multilingual (2)**: ko_reasoning, lang_hallucination_guard

### 확장 태스크 (2026-06-25)

| ID | harness | verify | 방식 |
|----|---------|--------|------|
| slide_outline_json | smoke | office | JSON 5장 슬라이드 구조 |
| slide_deck_create | agent | office | pptx_create E2E |
| proposal_rfp | smoke | none | Judge (제안서 초안) |
| proposal_json | smoke | office | JSON 섹션 키·RFP 키워드 |
| ad_copy_campaign | smoke | none | Judge (광고 3종) |
| ad_copy_json | smoke | office | JSON 길이·해시태그 |
| py_is_palindrome | smoke | swe | 코드 exec |
| py_fizzbuzz | smoke | swe | 코드 exec |
| py_clamp | smoke | swe | 코드 exec |

참고 벤치마크: HumanEval/MBPP(함수 구현), SWE-bench Lite(리포 버그픽스 — VEGA는 micro exec로 적응), PresentBench/SlidesGen-Bench(슬라이드), grant/RFP rubric 연구(제안서), marketing rubric(광고).

Excel fixture: `data/bench_fixtures/sales_wrong_sum.xlsx`

## Tool-calling requirements (agent harness)

Agent 벤치는 `stream_gpt` E2E에서 **도구 선택·호출**을 채점한다. `pipeline/streaming.py`가 `stats["tools_called"]`(도구명 리스트)와 `stats["tool_rounds"]`를 기록한다.

### 태스크 메타 필드

| 필드 | 설명 |
|------|------|
| `required_tools` | 최소 1회 호출 필수 (`xlsx_create`, `web_search`, `python_exec` 등) |
| `forbidden_tools` | 호출 시 실패 (office 기본: `host_exec`) |
| `min_tool_rounds` / `max_tool_rounds` | 도구 라운드 수 제약 |
| `harness: agent` | smoke 경로 없음 — agent 전용 |

### Pass criteria (agent)

```
pass = verify_pass (또는 judge_pass) AND tool_pass
```

- `verify_tool_use()` — `scripts/bench_lib.py`
- 결과 필드: `tools_called`, `required_tools_met`, `tool_score`, `tool_pass`, `tool_verify`

### Office task → required_tools map

| Task | Harness | required_tools | verify |
|------|---------|----------------|--------|
| excel_calc | both | `xlsx_create` | office |
| excel_create_e2e | agent | `xlsx_create` | office |
| excel_read_fix | agent | `xlsx_read` | office |
| excel_read_fix_save | agent | `xlsx_read`, `xlsx_create` | office |
| python_calc_xlsx | agent | `python_exec`, `xlsx_create` | office |
| slide_deck_create | agent | `pptx_create` | office |
| web_search_summarize | agent | `web_search` | judge |
| proposal_rfp_agent | agent | `web_search` | judge |
| ad_copy_research | agent | `web_search` | judge |
| press_release, pdf_summarize, biz_email, meeting_minutes, proposal_rfp, ad_copy_campaign | smoke | — | judge |
| slide_outline_json, proposal_json, ad_copy_json | smoke | — | office (JSON) |

VEGA 도구명은 `pipeline/tools.py` / `tools_office.py` / `tools_web.py` 스키마와 일치: `xlsx_create`, `xlsx_read`, `pptx_create`, `python_exec`, `web_search`, `web_fetch`, `file_read` 등.

## Verify-first

- `verify_swe()` — 코드 exec (SWE: py_*)
- `verify_office()` — JSON/xlsx/pptx artifact 검증
- `VERIFY_FIRST_TASKS` — verify pass 시 Judge 생략 (excel_calc, py_fizzbuzz, slide_outline_json, **python_calc_xlsx**, **excel_read_fix_save** 등)
- excel_calc / excel_create_e2e / **python_calc_xlsx** / **excel_read_fix_save** / **slide_deck_create** / **proposal_json** / **ad_copy_json**: **verify pass면 Judge 생략**

## Artifact schema v2

```json
{
  "schema_version": 2,
  "harness": "smoke|agent|merged",
  "results": [...],
  "summary": { "model_id": { "pass", "n", "mean_ratio" } },
  "summary_by_category": { "office": { "pass", "n", "mean_ratio" } },
  "summary_by_task": { "excel_calc": { ... } }
}
```

## Routing (`pipeline/model_catalog.py`)

- `load_bench_scores(category=..., harness=...)`
- `resolve_turn_model("standard")` → **office** agent bench median
- `resolve_turn_model("heavy")` → **swe** bench max
- `resolve_turn_model("light")` → office score + min price


## Excel winners (@excel)

`data/bench_excel_winners.json` — curated 26모델 스크린 (2026-06-25):

- **excel_calc smoke**: 14/26 pass (`verify_office`)
- **excel_create_e2e agent**: 7/14 pass (smoke 통과자 대상)
- **full excel (both)**: 7 models

| Model | out $/M | e2e lat |
|-------|---------|---------|
| `openai/gpt-oss-20b` | $0.14 | 7.8s |
| `openai/gpt-4.1-nano` | $0.40 | 4.2s |
| `google/gemini-2.5-flash-lite` | $0.40 | 7.2s |
| `openai/gpt-4o-mini` | $0.60 | 10.5s |
| `deepseek/deepseek-v4-pro` | $0.87 | 80s |

**실패 주목**: `deepseek-v4-flash`, `gemini-2.5-flash-lite-preview`, tier1 deepseek v3.x — excel_calc 또는 e2e fail.

```bash
python scripts/bench_models.py --models @excel --task-ids excel_calc
python scripts/bench_agent.py --models @excel --task-ids excel_create_e2e
```

## Tier-1 모델 (@tier1)

1차 `bench.json` **7/7** 중 latency 상위 4개 — `data/bench_tier1_models.json`:

1. `google/gemini-2.5-flash-lite-preview-09-2025`
2. `openai/gpt-4o-mini`
3. `deepseek/deepseek-v3.1-terminus`
4. `deepseek/deepseek-v3.2-exp`

전체 큐레이션 재측정: `--models @curated`

## Operator Runbook

```bash
# 1. Smoke — 전체 큐레이션 (@tier1), ~9 smoke tasks
python scripts/bench_models.py --models @tier1 --out build_output/bench_smoke.json

# 2. Agent — office E2E (비용 5–15×)
python scripts/bench_agent.py --models @tier1 --categories office --out build_output/bench_agent.json

# 3. Merge → routing consumer
python scripts/merge_bench_artifacts.py \
  --smoke build_output/bench_smoke.json \
  --agent build_output/bench_agent.json \
  --out build_output/bench.json
```

개발용:

```bash
python scripts/bench_models.py --dry-run
python scripts/bench_agent.py --dry-run --limit-models 3

# 확장 태스크 smoke only (신규 9개)
python scripts/bench_models.py --models @tier1 \
  --task-ids slide_outline_json,proposal_json,ad_copy_json,proposal_rfp,ad_copy_campaign,py_is_palindrome,py_fizzbuzz,py_clamp \
  --out build_output/bench_extended_smoke.json

# Tool-calling agent bench (@tier1)
python scripts/bench_agent.py --models @tier1 \
  --task-ids excel_create_e2e,excel_read_fix,excel_read_fix_save,python_calc_xlsx,web_search_summarize,slide_deck_create,proposal_rfp_agent,ad_copy_research \
  --out build_output/bench_toolcalling_agent.json
```

## External benchmarks (routing subset)

Ingest once:

```bash
python scripts/bench_external/ingest.py --suite all --routing
```

Run external suites (~123 tasks):

```bash
VEGA_BENCH_JUDGE=claude-cli python scripts/bench_external.py --suites routing --models @tier1 \
  --judge claude-cli --out build_output/bench_external_merged.json
```

Judge backends: `openrouter` (default, Sonnet via OR) or `claude-cli` (`claude -p`, subscription — saves ~$1.5–2 per full routing run).

Merge with native:

```bash
python scripts/merge_bench_artifacts.py \
  --smoke build_output/bench_smoke.json \
  --agent build_output/bench_agent.json \
  --external build_output/bench_external_merged.json \
  --out build_output/bench.json
```

Suites: `humaneval`, `mbpp`, `swebench_lite`, `presentbench`, `slidesgen`, `deckbench`, `odysseybench`, `officeeval`, `creativity`, `adbench`, `bizgeneval`.

Exclusions: `docs/bench_external_exclusions.md`. Optional wrappers: `scripts/bench_external/wrappers/`.

Artifact adds `summary_by_source` (suite-level pass rates).

## CI

- `tests/test_bench_harness.py` — bench_lib 순수 로직
- `tests/test_bench_external.py` — external ingest/load/verify
- `tests/test_bench_agent.py` — mock stream_gpt L2
- API 호출 없음 (dry-run + mock)
