# Memory search E2E verification commands

Created: 2026-06-21

## Unit: hybrid fusion

```bash
source ~/dev/mlx_env/bin/activate 2>/dev/null || true
VEGA_DATA_DIR=/private/tmp/vega_agent_pytest_data_hybrid pytest -q -p no:rerunfailures tests/test_hybrid_search.py
```

Output:

```text
..... [100%]
5 passed in 1.73s
```

Note: the first attempt without `VEGA_DATA_DIR` failed during collection because the sandbox cannot write the default home `vega.db`.

## E2E benchmark

```bash
source ~/dev/mlx_env/bin/activate 2>/dev/null || true
python scripts/verify_memory_search_e2e.py
```

Output:

```text
verdict: PASS
top5_pass_rate: fts5=60.0% hybrid=100.0% delta=40.0pp
wrote JSON: benchmark_reports/memory_search_e2e/memory_search_e2e_verification.json
wrote report: benchmark_reports/memory_search_e2e/memory_search_e2e_verification.md
```

## vega_query, server routers, inspector endpoints

```bash
source ~/dev/mlx_env/bin/activate 2>/dev/null || true
VEGA_DATA_DIR=/private/tmp/vega_agent_pytest_data_memory_single pytest -q -n 0 -p no:rerunfailures tests/test_vega_query.py tests/test_memory_inspector.py tests/test_server_routers.py
```

Output:

```text
.................................................................................. [100%]
82 passed in 1.12s
```

Note: the parallel `-n auto` run hit a shared tmp DB migration race (`duplicate column name`) and was rerun with `-n 0`; the single-process run passed.

## LanceDB rebuild dry-run

```bash
source ~/dev/mlx_env/bin/activate 2>/dev/null || true
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python scripts/rebuild_memory_vectors.py --log-level INFO
```

Output:

```text
INFO source_table=memories source_rows=1 expected_dim=1024
INFO reindexed_rows=1 dimension_validation=passed
INFO dry_run=true commit=false archive_created=false table_replaced=false
```

## Syntax check

```bash
source ~/dev/mlx_env/bin/activate 2>/dev/null || true
python -m py_compile pipeline/hybrid_search.py scripts/verify_memory_search_e2e.py tests/test_hybrid_search.py
```

Output: exited 0 with no diagnostics.
