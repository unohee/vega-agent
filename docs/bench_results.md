# Bench Results Summary

Local run artifacts live under `build_output/` (gitignored). This page summarizes what exists as of **2026-06-25** on branch `fix/overthinking-roadmap`.

Related docs:

- Design & runbook: [bench_design.md](./bench_design.md)
- External suite exclusions: [bench_external_exclusions.md](./bench_external_exclusions.md)

## Status at a glance

| Run | Artifact | Harness | Aggregate | Models | Notes |
|-----|----------|---------|-----------|--------|-------|
| Tier-1 smoke | `build_output/bench_smoke.json` | smoke | **28/36 (78%)** | 4 (@tier1) | 9 tasks · routing baseline |
| Tier-1 agent (legacy) | `build_output/bench_agent.json` | agent | **5/16 (31%)** | 4 | 4 office tasks · superseded by tool-calling run |
| **Merged routing** | `build_output/bench.json` | merged | **344/575 (60%)** | 4 | smoke + tool-calling agent + fixed external · consumed by `model_catalog` (2026-06-25) |
| Extended smoke | `build_output/bench_extended_smoke.json` | smoke | **27/32 (84%)** | 4 | 8 new tasks (slides, proposal, ad, py_*) |
| Tool-calling agent | `build_output/bench_toolcalling_agent.json` | agent | **15/32 (47%)** | 4 | 8 office tasks with `required_tools` |
| Excel smoke | `build_output/bench_excel_smoke.json` | smoke | **14/26 (54%)** | 26 (@excel) | `excel_calc` only |
| Excel agent | `build_output/bench_excel_agent.json` | agent | **7/14 (50%)** | 14 | smoke-pass subset · `excel_create_e2e` |
| External HumanEval pilot | `build_output/bench_external_humaneval_pilot.json` | external | **3/3 (100%)** | 1 | 3-task smoke check |
| **External routing (full, pre-fix)** | `build_output/bench_external_merged.json` | external | **206/492 (42%)** | 4 (@tier1) | 123 tasks · 11 suites · `claude-cli` judge · 2026-06-25 |
| **External (harness-fixed)** | `build_output/bench_external_fixed.json` | external | **301/507 (59%)** | 4 (@tier1) | full + re-run MBPP/SWE/Odyssey w/ INT-1920~1923 fixes |
| External re-run (affected) | `build_output/bench_external_affected.json` | external | **26/180 (14%)** | 4 | MBPP+SWE+Odyssey · pre-MBPP-prompt-fix |
| External MBPP re-fix | `build_output/bench_external_mbpp_refix.json` | external | **77/80 (96%)** | 4 | MBPP after prompt-includes-tests fix |
| Extended agent pilot | `build_output/bench_extended_agent.json` | agent | **1/2 (50%)** | 2 | `slide_deck_create` only |
| External merge test | `build_output/test_ext_merge.json` | external | 1/1 | mock | dev fixture only |

## Tier-1 models (@tier1)

Used in most runs above (`data/bench_tier1_models.json`):

1. `google/gemini-2.5-flash-lite-preview-09-2025`
2. `openai/gpt-4o-mini`
3. `deepseek/deepseek-v3.1-terminus`
4. `deepseek/deepseek-v3.2-exp`

## Merged routing (`bench.json`)

Current production merge for `pipeline/model_catalog.py` (smoke + tool-calling agent + **harness-fixed external**). 575 results.

**By category:** office 122/307 (0.557) · **swe 180/208 (0.883)** · multilingual 8/8 (1.0) · creative 34/52 (0.752)

`model_catalog` per-category scores (mean ratio):

| Model | swe | office |
|-------|-----|--------|
| `deepseek/deepseek-v3.1-terminus` | 0.904 | 0.636 |
| `deepseek/deepseek-v3.2-exp` | 0.897 | 0.644 |
| `google/gemini-2.5-flash-lite-preview-09-2025` | 0.885 | 0.490 |
| `openai/gpt-4o-mini` | 0.846 | 0.374 |

→ heavy(swe) routing: `deepseek-v3.1-terminus` (0.904). standard/light(office): `deepseek-v3.2-exp` (0.644).

Updated: 2026-06-25 (harness-fix re-run)

## Tier-1 smoke (`bench_smoke.json`)

| Model | Pass | Rate |
|-------|------|------|
| `openai/gpt-4o-mini` | 8/9 | 89% |
| `google/gemini-2.5-flash-lite-preview-09-2025` | 7/9 | 78% |
| `deepseek/deepseek-v3.1-terminus` | 7/9 | 78% |
| `deepseek/deepseek-v3.2-exp` | 6/9 | 67% |

**By category:** office 13/20 · swe 7/8 · multilingual 8/8

**Hardest tasks:** `biz_email_reply` 0/4 · `excel_calc` 1/4

Updated: 2026-06-25 00:45

## Tool-calling agent (`bench_toolcalling_agent.json`)

Replaces the legacy 4-task agent run for office routing ground truth.

| Model | Pass | Rate |
|-------|------|------|
| `deepseek/deepseek-v3.2-exp` | 5/8 | 62% |
| `google/gemini-2.5-flash-lite-preview-09-2025` | 4/8 | 50% |
| `deepseek/deepseek-v3.1-terminus` | 4/8 | 50% |
| `openai/gpt-4o-mini` | 2/8 | 25% |

**By category:** office 15/32

**Task pass rates:** `slide_deck_create` 4/4 · `excel_create_e2e` 3/4 · `proposal_rfp_agent` 2/4 · `ad_copy_research` 2/4 · `excel_read_fix` 1/4 · `excel_read_fix_save` 1/4 · `python_calc_xlsx` 1/4 · `web_search_summarize` 1/4

Updated: 2026-06-25 10:55

## Extended smoke (`bench_extended_smoke.json`)

Nine new/expanded smoke tasks (slides, RFP, ad copy, py micro-SWE).

| Model | Pass | Rate |
|-------|------|------|
| `openai/gpt-4o-mini` | 8/8 | 100% |
| `deepseek/deepseek-v3.1-terminus` | 7/8 | 88% |
| `google/gemini-2.5-flash-lite-preview-09-2025` | 6/8 | 75% |
| `deepseek/deepseek-v3.2-exp` | 6/8 | 75% |

**By category:** office 15/20 · swe 12/12 (py_is_palindrome, py_fizzbuzz, py_clamp all 4/4)

**Weaker office tasks:** `proposal_rfp` 2/4 · `ad_copy_json` 2/4 · `ad_copy_campaign` 3/4

Updated: 2026-06-25 09:46

## Excel screen (@excel)

Curated 26-model screen; winners documented in [bench_design.md](./bench_design.md#excel-winners-excel).

### Smoke — `excel_calc` (`bench_excel_smoke.json`)

**14/26 pass (54%)** — verify-first xlsx artifact check.

Notable passes: `openai/gpt-oss-20b`, `openai/gpt-4.1-nano`, `google/gemini-2.5-flash-lite`, `openai/gpt-4o-mini`, `deepseek/deepseek-v4-pro`, several gemma/gpt-oss variants.

Notable fails: `deepseek/deepseek-v3.1-terminus`, `google/gemini-2.5-flash-lite-preview-09-2025`, `deepseek/deepseek-v4-flash`, tier-1 deepseek v3.x variants.

Updated: 2026-06-25 01:03

### Agent — `excel_create_e2e` (`bench_excel_agent.json`)

**7/14 pass (50%)** among smoke-pass subset.

Full excel (smoke + agent): **7 models** — see design doc table (`gpt-oss-20b`, `gpt-4.1-nano`, `gemini-2.5-flash-lite`, `gpt-4o-mini`, `deepseek-v4-pro`, etc.).

Updated: 2026-06-25 01:07

## External benchmarks

### Office mutation 모델 cut 탐색 (2026-06-25)

OdysseyBench Excel 변형(delete/sort/swap) 9-task로 **모델 능력 cut**을 탐색 — tier1(≤$0.6, 22%)이 약한 게 가격/능력 한계인지 확인. Claude·GPT·Gemini의 mid+flagship 6개 측정(judge 0, 결정적 verify).

| $/M out | model | pass |
|---|---|---|
| 1.5 | google/gemini-3.1-flash-lite | 3/9 (33%) |
| 4.5 | openai/gpt-5.4-mini | 1/9 (11%) |
| 5 | anthropic/claude-haiku-4.5 | 3/9 (33%) |
| 12 | google/gemini-3.1-pro-preview | 2/9 (22%) |
| 15 | anthropic/claude-sonnet-4.6 | 2/9 (22%) |
| 30 | openai/gpt-5.5 | 3/9 (33%) |

**cut 없음** — $1.5→$30 전 구간 11~33%로 tier1(22%)과 동급. 가격이 pass를 예측하지 못함.

**단, 이유는 모델 무능이 아니라 verify 과엄격**: 단일 골든 `exact_match`가 유효한 대안 해석을 거부. 증거 — task 002 "delete all amounts in salary": 골든은 `amount` **열 통째 제거**, 그런데 gpt-5.5·claude-sonnet-4.6은 **값만 비우고 헤더 유지**(둘 다 합당) → `cells_differ` fail. 모호 삭제 task(000/001/002)는 frontier 포함 전부 0/6, 명확 task(005 정렬 5/6·003 전체삭제 4/6)는 가격 무관 통과.

**함의**:
1. **라우팅**: office 변형은 고가 모델 불필요 — frontier ≈ cheap. "office는 $1 캡을 깨야 한다"는 기우, ≤$1 풀로 충분.
2. **자체 벤치 필요**: 이식 OdysseyBench의 single-golden exact_match는 모호 태스크에서 천장을 만들어 cut 측정 도구로 부적합. 제대로 측정하려면 (a) 의미동등 허용 verify, 또는 (b) 명확-골든 태스크만 큐레이션. → VEGA 자체 office 벤치 설계 과제.

아티팩트: `build_output/bench_odyssey_cgg.json` (gitignore, 로컬).

### Harness-fix re-run (INT-1920~1923, 2026-06-25)

Full external run의 비정상 저점수가 모델 약점이 아니라 harness false-negative였음을 진단·수정 후 재측정. HumanEval 98%인데 MBPP 0%면 채점 버그가 거의 확실 (같은 코드생성).

| Suite | pre-fix | post-fix | 원인 / 수정 |
|-------|---------|----------|-------------|
| **MBPP** | 0–5% | **96%** (77/80) | 프롬프트가 함수명 미노출 → NameError. test_list assertion을 프롬프트에 포함 (+ double-assert 제거). INT-1920 |
| **SWE-lite** | 5% | **45%** (18/40) | exec_pass인데 judge fail. swebench_lite를 verify-first에 추가. INT-1921 |
| Odyssey | 4% | **22%** (8/36) | **실데이터 재구축** — 입력 미제공 합성 스텁을 OdysseyBench(스펙·골든)+OfficeBench(입력) 실데이터 9-Excel subset으로 교체. 결정적 `exact_match` verify(judge 불필요). 22%는 **진짜 신호** — tier1 모델이 정밀 xlsx 변형(삭제/스왑)에 실제로 약함 |
| HumanEval | 98% | 98% | 변화 없음 (이미 정상) |

### OdysseyBench 재구축 상세 (실데이터 이식)

기존 `ingest_odysseybench`는 입력 데이터 없는 합성 스텁 15개(태스크 제목 + required_tool뿐)라 모델이 수행 불가 → 6% 허위 저점수. 실데이터로 교체:

- **출처**: [microsoft/OdysseyBench](https://github.com/microsoft/OdysseyBench)(MIT, subtask 지시문+골든) + [zlwang-cs/OfficeBench](https://github.com/zlwang-cs/OfficeBench)(Apache-2.0, testbed 입력 xlsx). attribution: `data/bench_external/odysseybench/NOTICE.txt`.
- **9 Excel subset**: delete×5, sort×1, swap×3 (변별력 없는 degenerate 1건 제외).
- **결정적 verify**: `verify_odyssey_eval`(`bench_lib.py`) — 에이전트 출력 xlsx를 골든과 셀 단위 `exact_match` 비교. judge 불필요(verify-first). offline 검증: 골든→pass·입력→fail 9/9.
- **결과**: 8/36 (22%). task별 sort 4/4·delete-all 3/4은 통과, 정밀 cell 삭제·스왑은 대부분 0/4 — 모델 출력=입력(변형 미수행). 실제 능력 한계 반영.

| Model | pass |
|-------|------|
| `deepseek-v3.1-terminus` | 3/9 |
| `deepseek-v3.2-exp` | 2/9 |
| `gpt-4o-mini` | 2/9 |
| `gemini-2.5-flash-lite` | 1/9 |

수정 후 `bench_external_fixed.json` = full run에 위 3 suite 덮어쓰기. merge 후 **swe category mean 0.883** (이전 MBPP 0%가 끌어내리던 값 정상화). `model_catalog` swe 점수: deepseek-v3.1 0.90 · v3.2 0.90 · gemini 0.89 · gpt-4o-mini 0.85.

진단: `scripts/bench_harness_triage.py` (API 없이 게이트 검증).

### HumanEval pilot (`bench_external_humaneval_pilot.json`)

| Model | Pass | Rate |
|-------|------|------|
| `google/gemini-2.5-flash-lite-preview-09-2025` | 3/3 | 100% |

Suite: **humaneval** (tasks `ext_humaneval_000`–`002`) · harness smoke · code exec verify.

Updated: 2026-06-25 11:08

### Pending

- Full routing external run → `bench_external_merged.json`
- Other suites ingested under `data/bench_external/` (mbpp, swebench_lite, presentbench, slidesgen, deckbench, odysseybench, officeeval, creativity, adbench, bizgeneval) — **no full @tier1 results yet**

Re-run after ingest:

```bash
VEGA_BENCH_JUDGE=claude-cli python scripts/bench_external.py \
  --suites routing --models @tier1 --judge claude-cli \
  --out build_output/bench_external_merged.json
```

## Non-routing artifacts (same folder)

These JSON files are **not** part of the model-routing bench merge:

| File | Purpose |
|------|---------|
| `overthinking_baseline.json` | INT-1893 overthinking A/B (schema v1) |
| `overthinking_final.json` | INT-1893 overthinking A/B (schema v1) |
| `int1893_before_after.json` | INT-1893 scenario metadata |
| `test_ext_merge.json` | External merge unit-test fixture |

## External routing full run (`bench_external_merged.json`)

Completed **2026-06-25** (~3.1 h wall time). Command:

```bash
VEGA_BENCH_JUDGE=claude-cli python3 scripts/bench_external.py \
  --suites routing --models @tier1 --judge claude-cli \
  --out build_output/bench_external_merged.json
```

492 runs (123 tasks × 4 models). Judge: `claude-cli` (local `claude -p`). Log: `build_output/bench_external_run.log`.

### By model (123 tasks each)

| Model | Pass | Rate |
|-------|------|------|
| `deepseek/deepseek-v3.2-exp` | 63/123 | 51% |
| `deepseek/deepseek-v3.1-terminus` | 62/123 | 50% |
| `google/gemini-2.5-flash-lite-preview-09-2025` | 54/123 | 44% |
| `openai/gpt-4o-mini` | 27/123 | 22% |

### By source suite (all models pooled)

| Suite | Pass | N | mean_ratio | Notes |
|-------|------|---|------------|-------|
| humaneval | 78 | 80 | 0.98 | Strong |
| bizgeneval | 12 | 12 | 1.00 | Perfect |
| adbench | 9 | 15 | 0.87 | |
| presentbench | 44 | 56 | 0.79 | |
| creativity | 22 | 40 | 0.68 | |
| officeeval | 19 | 30 | 0.67 | |
| deckbench | 9 | 15 | 0.72 | |
| slidesgen | 9 | 30 | 0.30 | Weak |
| odysseybench | 2 | 45 | 0.22 | Weak |
| swebench_lite | 2 | 40 | 0.20 | Weak |
| mbpp | 0 | 80 | 0.00 | **All fail** — verify harness/ingest |
| native | 0 | 49 | 0.00 | Placeholder rows in merge schema |

**Takeaways:** HumanEval/bizgen/ad copy suites track tier-1 well; MBPP 0% and SWE/Odyssey/SlidesGen agent tasks need harness or task review before routing merge.

## Recommended next merge

Refresh routing input with tool-calling agent + external full run:

```bash
python scripts/merge_bench_artifacts.py \
  --smoke build_output/bench_smoke.json \
  --agent build_output/bench_toolcalling_agent.json \
  --external build_output/bench_external_merged.json \
  --out build_output/bench.json
```

Until then, `model_catalog` reads the existing merged artifact (legacy agent path).

## Regenerating this summary

```bash
# Quick aggregate from local JSON (does not commit build_output)
python3 -c "
import json; from pathlib import Path
for p in sorted(Path('build_output').glob('bench*.json')):
    d=json.load(open(p)); s=d.get('summary',{})
    tp=sum(v.get('pass',0) for v in s.values()); tn=sum(v.get('n',0) for v in s.values())
    print(f'{p.name}: {tp}/{tn} harness={d.get(\"harness\")}')
"
```
