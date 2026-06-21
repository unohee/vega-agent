# Retrieval Method Gate Report

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
