# Retrieval Method Gate Report

> ⚠️ **INVALID — DO NOT ACT ON THIS GATE DECISION (B5 / INT-1686).**
> This benchmark never actually ran the BGE-M3 side. BGE-M3 scored 0/75 because
> the embedding path failed with `ModuleNotFoundError: TrainerCallback` and the
> LanceDB vector table was empty (`loaded_text_rows=1` vs `sqlite_rows=45807`),
> not because semantic retrieval underperformed. The query set below is also
> blank. The "FAIL → stop later implementation subtasks" conclusion is therefore
> meaningless and must NOT be used to remove or halt BGE-M3 / hybrid search work.
> Re-run only after fixing the embedding dependency (INT-1690/H1) and the bench
> dimension config (INT-1696/H7), then replace this file with the real result.

## Query Set
- 
- 
- 
- 
- 
- 
- 
- 
- 
- 
- 
- 
- 
- 
- 

## Relevance Scoring Rubric
Top-5 relevance measured by number of candidates returned in top-5.
Aggregate relevance = (total candidates) / (total possible)

## Per-Query Relevance
| Query | FTS5 Top-5 Hits | BGE-M3 Top-5 Hits |
|---|---|---|
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |
|  | 5 | 0 |

## Aggregate Relevance
- FTS5: 100.0% (75/75)
- BGE-M3: 0.0% (0/75)
- Delta: **-100.0 percentage points**

## Backend & Latency Notes
- sentence-transformers: available (MPS)
- fastembed: not available
- MLX: detected but not used in this benchmark

## Gate Decision
❌ **FAIL**: BGE-M3 does not improve top-5 relevance by >=10 percentage points.
Stop later implementation subtasks.
