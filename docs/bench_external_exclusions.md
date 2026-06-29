# External benchmark exclusions (INT-1876)

VEGA routing subset adapts public benchmarks to `stream_gpt` + office tools. Some upstream suites are **not fully portable**.

Design & runbook: [bench_design.md](./bench_design.md) · Local run summary: [bench_results.md](./bench_results.md)

| Bench | Exclusion | VEGA alternative |
|-------|-----------|------------------|
| **BizGenEval (image)** | T2I commercial layout; needs image generation APIs | 3 smoke tasks: slide-text JSON layout (`bizgeneval` suite) |
| **AD-Bench (production)** | Live advertising platform + dynamic GT | 5 tasks: `adbench/fixtures/campaigns.csv` + `python_exec` mock analytics |
| **OfficeEval (Word/PPT COM)** | Windows win32com automation | 10 Excel-only `xlsx_create` tasks with `officeeval_spec` verify |
| **Creativity Benchmark (human)** | 678 practitioner pairwise comparisons | 10 smoke tasks + LLM judge rubric (weak correlation vs human) |
| **PresentBench (full)** | 50+ checklist items per instance | 15 adapted tasks; optional `presentbench_checklist` wrapper |
| **SWE-bench Lite (full)** | Docker repo checkout per instance | 10 micro `swebench_lite` agent tasks |
| **SlidesGen-Bench (visual metrics)** | Requires rendered bitmap pipeline | `pptx_create` verify + optional `BENCH_SLIDESGEN_ROOT` scorer |

## Optional wrappers

- `scripts/bench_external/wrappers/slidesgen_scorer.py` — set `BENCH_SLIDESGEN_ROOT`
- `scripts/bench_external/wrappers/presentbench_checklist.py` — LLM checklist pass
- `scripts/bench_external/wrappers/swebench_native.py` — Docker placeholder

## Full upstream runs

Operator may run full HumanEval/MBPP via:

```bash
python scripts/bench_external/ingest.py --suite humaneval --limit 164
python scripts/bench_external.py --suites humaneval --models @curated --harness smoke
```

Routing default remains manifest `routing_limit` per suite (~123 tasks total).
