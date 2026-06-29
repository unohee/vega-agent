# External benchmark ingest

Routing subsets for VEGA bench harness. Licenses per suite in `data/bench_external/manifest.json`.

## Ingest

```bash
python scripts/bench_external/ingest.py --suite all --routing
```

Output: `data/bench_external/{suite}/tasks.jsonl`

## Full vs routing

- `--routing` (default when no `--limit`): use `routing_limit` from manifest (~120 tasks total)
- `--limit N`: override per suite
- `--limit 0` with manifest cap 9999: operator full ingest (HumanEval/MBPP download all available)

## Suites

| Suite | Tasks (routing) | Harness |
|-------|-----------------|---------|
| humaneval | 20 | smoke |
| mbpp | 20 | smoke |
| swebench_lite | 10 | agent |
| presentbench | 15 | agent |
| slidesgen | 10 | agent |
| deckbench | 5 | agent |
| odysseybench | 15 | agent |
| officeeval | 10 | agent |
| creativity | 10 | smoke |
| adbench | 5 | agent |
| bizgeneval | 3 | smoke |

Exclusions: see `docs/bench_external_exclusions.md`.
