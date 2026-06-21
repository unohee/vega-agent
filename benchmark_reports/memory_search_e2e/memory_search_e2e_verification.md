# Memory search E2E verification

- Created: 2026-06-21T13:01:13.611444+00:00
- SQLite source: `/Users/unohee/Library/Application Support/VEGA/vega.db`
- SQLite verification copy: `/private/tmp/vega_memory_verify_0bul9atq/vega.db`
- LanceDB: `/Users/unohee/Library/Application Support/VEGA/lancedb`
- Top-k: 5
- Gate: hybrid top-5 pass rate must beat FTS5-only by >= 10.0 pp.

## Verdict

**PASS** - delta 40.0 pp (FTS5 60.0%, hybrid 100.0%).

## Regression checks

- Import check: `PASS`; memory_store embedder after server import: `None`
- LanceDB rows/dimension: 1 rows, vector_dim=1024
- Embedding cosine sanity: `PASS`; target=0.7804, unrelated=0.2669
- SHA256 embedding fallback hits in scoped files: 0
- Inspector smoke: `PASS`

## Query results

| kind | query | expected | FTS5 pass | hybrid pass | hybrid top hit |
|---|---|---|---:|---:|---|
| semantic | KYTE 음악 라이선스 AI 인프라 담당자 | 오희원은 KYTE에서 음악 라이선스 AI 인프라를 담당한다 | no | yes | test:5a66bbe34deacfb38034146801dfe0a6 |
| semantic | KYTE 음악 라이선스 AI 인프라 | 오희원은 KYTE에서 음악 라이선스 AI 인프라를 담당한다 | no | yes | test:5a66bbe34deacfb38034146801dfe0a6 |
| semantic | 오희원 KYTE 음악 라이선스 AI 인프라 | 오희원은 KYTE에서 음악 라이선스 AI 인프라를 담당한다 | no | yes | test:5a66bbe34deacfb38034146801dfe0a6 |
| semantic | 음악 라이선스 AI 인프라 담당 | 오희원은 KYTE에서 음악 라이선스 AI 인프라를 담당한다 | no | yes | test:5a66bbe34deacfb38034146801dfe0a6 |
| proper_noun | KYTE AX | KYTE AX | yes | yes | entities:1207 |
| proper_noun | VEGA Tauri App | VEGA Tauri App | yes | yes | entities:3745 |
| proper_noun | de-artifact | de-artifact | yes | yes | entities:972 |
| proper_noun | STONKS | STONKS | yes | yes | entities:2618 |
| proper_noun | ArtifactNet | ArtifactNet | yes | yes | entities:748 |
| proper_noun | LanceDB | LanceDB | yes | yes | entities:89 |

## Commands

```bash
python scripts/verify_memory_search_e2e.py
```
