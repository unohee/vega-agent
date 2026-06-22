# LLM-Driven Memory Curation (INT-1826)

Memory stack = **lexical FTS5 + persona SQL injection + LLM curation**. Vector embedding is frozen (INT-1828); re-evaluate only at scale/quality triggers.

## Recall

- **Base**: `memory_search` (FTS5/BM25 via `pipeline/vega_query.py`). Korean unicode61 + prefix matching.
- **LLM query expansion**: the agent issues synonym/reformulation variants (e.g. "트레이딩" ↔ "매매") and merges the result sets. This covers lexical search's semantic gap without embeddings — the LLM, not a vector model, supplies the "semantic" layer.
- **Persona**: always injected into the system prompt via `get_persona` (SQL-only, embedding-independent).

## Hygiene loop

Triggered at compaction (every ~20 turns, `pipeline/compaction.py`) and periodically (heartbeat):

1. **Promote** — durable facts from the compaction summary into `persona_sections` / `events`.
2. **Merge** — deduplicate near-identical memories (same entity/claim).
3. **Correct** — flag and revise stale facts when newer evidence appears.
4. **Forget** — salience-weighted decay of low-value working memory (ties into INT-1500 short/long tiering).

## Tools

Reuse the existing memory-management tools (list / search / get / diff / archive / delete) as the curation primitives — the loop orchestrates them, no new native tools required.

## Non-goals

- Vector embedding (frozen). If recall quality is insufficient, strengthen LLM query expansion first; only at scale (10⁵+ memories) reconsider cloud embeddings (OpenRouter bge-m3, $0.01/1M — verified) behind an explicit privacy consent gate.

## References

- Decision: `feedback_memory_llm_curation` memory, INT-1828 (embedding removal).
- Tiering: INT-1500.
