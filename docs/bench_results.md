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
| **Merged routing** | `build_output/bench.json` | merged | **33/52 (64%)** | 4 | smoke + legacy agent · consumed by `model_catalog` |
| Extended smoke | `build_output/bench_extended_smoke.json` | smoke | **27/32 (84%)** | 4 | 8 new tasks (slides, proposal, ad, py_*) |
| Tool-calling agent | `build_output/bench_toolcalling_agent.json` | agent | **15/32 (47%)** | 4 | 8 office tasks with `required_tools` |
| Excel smoke | `build_output/bench_excel_smoke.json` | smoke | **14/26 (54%)** | 26 (@excel) | `excel_calc` only |
| Excel agent | `build_output/bench_excel_agent.json` | agent | **7/14 (50%)** | 14 | smoke-pass subset · `excel_create_e2e` |
| External HumanEval pilot | `build_output/bench_external_humaneval_pilot.json` | external | **3/3 (100%)** | 1 | 3-task smoke check |
| Extended agent pilot | `build_output/bench_extended_agent.json` | agent | **1/2 (50%)** | 2 | `slide_deck_create` only |
| External merge test | `build_output/test_ext_merge.json` | external | 1/1 | mock | dev fixture only |

**Not yet run:** full external routing merge (`build_output/bench_external_merged.json`, ~123 tasks across 11 suites). Ingest manifest exists at `data/bench_external/manifest.json`.

## Tier-1 models (@tier1)

Used in most runs above (`data/bench_tier1_models.json`):

1. `google/gemini-2.5-flash-lite-preview-09-2025`
2. `openai/gpt-4o-mini`
3. `deepseek/deepseek-v3.1-terminus`
4. `deepseek/deepseek-v3.2-exp`

## Merged routing (`bench.json`)

Current production merge for `pipeline/model_catalog.py` (smoke + legacy agent, **no external**).

| Model | Pass | Rate |
|-------|------|------|
| `openai/gpt-4o-mini` | 9/13 | 69% |
| `deepseek/deepseek-v3.1-terminus` | 9/13 | 69% |
| `deepseek/deepseek-v3.2-exp` | 8/13 | 62% |
| `google/gemini-2.5-flash-lite-preview-09-2025` | 7/13 | 54% |

**By category:** office 18/36 · swe 7/8 · multilingual 8/8

**Hardest tasks:** `biz_email_reply` 0/4 · `excel_read_fix` 0/4 · `excel_calc` 1/8

Updated: 2026-06-25 00:54

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

## Recommended next merge

When tool-calling agent + external pilots are stable, refresh routing input:

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
