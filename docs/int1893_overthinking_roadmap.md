# INT-1893 Overthinking — baseline vs final

Epic [INT-1876](https://linear.app/intrect/issue/INT-1876) / [INT-1893](https://linear.app/intrect/issue/INT-1893)

## Before (routing-only fix, PR #57)

| Scenario | load | max_rounds |
|----------|------|------------|
| ikea multitrn | light | 10 |
| legacy blob ikea | heavy | 24 |

Gap: API budget, tool subset, tool_round cap unchanged → overthinking inside each round.

## After (this branch — full roadmap)

| Layer | Change |
|-------|--------|
| Phase 1 | `LOAD_BUDGET` → light `max_tokens=1200`, `reasoning_effort=low` |
| Phase 2 | light tool allowlist, `max_tool_rounds=2`, early stop after search |
| Phase 3 | `_light.md` fragment, duplicate `web_search` block |
| Phase 4 | UI load chips (⚡/⚖/🧠), telemetry DB, optional `VEGA_LOAD_CLASSIFIER` |

## Commands

```bash
# L1 routing table (no API)
python scripts/measure_load_rounds.py

# L3 scenario bench (dry-run + mock live)
python scripts/bench_overthinking.py --dry-run --phase baseline --out build_output/overthinking_baseline.json
python scripts/bench_overthinking.py --live --phase final --out build_output/overthinking_final.json

# L4 production report
python scripts/report_overthinking.py 7
```

## Tests (CI)

```bash
pytest tests/test_routing.py tests/test_int1893_overthinking.py \
  tests/test_load_budget.py tests/test_load_tool_filter.py \
  tests/test_streaming_load.py tests/test_web_search_dedup.py -q
```

## Done-when (INT-1893)

- [x] 멀티턴 + ikea → `max_rounds=10`, `max_tool_rounds=2`
- [x] before/after 표 (`measure_load_rounds`, this doc)
- [x] stats: `load`, `max_rounds`, `actual_rounds`, `tool_rounds`, `output_tokens`
- [x] scenario bench artifact (`overthinking_*.json`)
- [ ] Live OpenRouter median gate (operator runs when budget available)

## Epic INT-1876 checklist (remaining)

- [ ] Full model bench RUN → `build_output/bench.json` (operator)
- [x] Overthinking INT-1893 implementation
- [ ] INT-1894/1895 runtime triggers (separate issues)
