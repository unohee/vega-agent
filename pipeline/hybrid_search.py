# Created: 2026-06-21
# Purpose: Hybrid memory search — FTS5/BM25 + vector RRF fusion with source-aware weights.
# Dependencies: pipeline.vega_query, pipeline.memory_store
# Test Status: tests/test_hybrid_search.py

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from typing import Any

from pipeline import memory_store, vega_query

DEFAULT_RRF_K = 60
DEFAULT_LEXICAL_TABLES = ("entities", "events", "persona_sections", "messages")

_SOURCE_ALIASES = {
    "chat": "messages",
    "message": "messages",
    "messages_fts": "messages",
    "memory": "messages",
    "memories": "messages",
    "event": "events",
    "events_fts": "events",
    "entity": "entities",
    "entities_fts": "entities",
    "persona": "persona_sections",
    "persona_section": "persona_sections",
    "persona_sections_fts": "persona_sections",
}

_SOURCE_WEIGHTS = {
    "entities": (1.45, 0.55, "entities/events favor lexical: proper names, aliases, and tickers need exact-match precision"),
    "events": (1.30, 0.70, "entities/events favor lexical: dates, titles, and tags need exact-match precision"),
    "persona_sections": (0.70, 1.30, "persona_sections favor semantic: paraphrases of profile facts should outrank exact token overlap"),
    "messages": (1.00, 1.00, "generic messages are balanced between lexical and semantic evidence"),
}

LexicalSearcher = Callable[[str, str, int], list[dict[str, Any]]]
VectorSearcher = Callable[..., list[dict[str, Any]]]


def source_category(source: Any) -> str:
    raw = str(source or "messages").strip().lower()
    return _SOURCE_ALIASES.get(raw, raw)


def source_weights(source: Any) -> tuple[float, float, str]:
    category = source_category(source)
    return _SOURCE_WEIGHTS.get(
        category,
        (1.00, 1.00, "unknown/generic source is balanced between lexical and semantic evidence"),
    )


def _stable_key(source: str, row_id: Any, text: str = "") -> str:
    category = source_category(source)
    if row_id not in (None, ""):
        return f"{category}:{row_id}"
    digest = hashlib.sha1(f"{category}\0{text}".encode("utf-8")).hexdigest()[:16]
    return f"{category}:sha1:{digest}"


def _rrf(rank: int | None, weight: float, rrf_k: int) -> float:
    if rank is None:
        return 0.0
    return weight / (rrf_k + rank)


def _put_base(fields: dict[str, Any], source: str, row_id: Any, text: str, snippet: str = "") -> None:
    fields.setdefault("source", source_category(source))
    fields.setdefault("id", row_id)
    if text and not fields.get("text"):
        fields["text"] = text
    if snippet and not fields.get("snippet"):
        fields["snippet"] = snippet


def _merge_lexical(acc: dict[str, dict[str, Any]], row: dict[str, Any], rank: int) -> None:
    source = source_category(row.get("table") or row.get("source"))
    text = str(row.get("text") or "")
    row_id = row.get("id")
    key = _stable_key(source, row_id, text)
    item = acc.setdefault("key", {}) if False else acc.setdefault(key, {"stable_key": key})
    _put_base(item, source, row_id, text, str(row.get("snippet") or ""))
    item["lexical_rank"] = min(rank, item.get("lexical_rank", rank))
    item["lexical_score"] = float(row["bm25"]) if row.get("bm25") is not None else None
    item["lexical_source"] = row.get("source")


def _merge_vector(acc: dict[str, dict[str, Any]], row: dict[str, Any], rank: int) -> None:
    source = source_category(row.get("source"))
    text = str(row.get("text") or "")
    row_id = row.get("id")
    key = _stable_key(source, row_id, text)
    item = acc.setdefault(key, {"stable_key": key})
    _put_base(item, source, row_id, text)
    if row.get("timestamp") is not None:
        item.setdefault("timestamp", row.get("timestamp"))
    item["vector_rank"] = min(rank, item.get("vector_rank", rank))
    item["vector_score"] = float(row["score"]) if row.get("score") is not None else None
    item["vector_source"] = row.get("source")


def _collect_lexical(
    query: str,
    tables: Iterable[str],
    top_k: int,
    searcher: LexicalSearcher,
) -> list[tuple[dict[str, Any], int]]:
    ranked: list[tuple[dict[str, Any], int]] = []
    for table in tables:
        rows = searcher(table, query, top_k)
        ranked.extend((row, rank) for rank, row in enumerate(rows, start=1))
    return ranked


def hybrid_search(
    query: str,
    *,
    person_id: str | None = None,
    limit: int = 5,
    lexical_top_k: int = 20,
    vector_top_k: int = 20,
    lexical_tables: Iterable[str] = DEFAULT_LEXICAL_TABLES,
    rrf_k: int = DEFAULT_RRF_K,
    lexical_searcher: LexicalSearcher | None = None,
    vector_searcher: VectorSearcher | None = None,
) -> list[dict[str, Any]]:
    """Fuse vega_query FTS5/BM25 rows and memory_store vector rows via weighted RRF.

    Returned rows expose lexical_rank/score, vector_rank/score, fused_score, stable_key,
    and source_weight_explanation. BM25/vector distances are kept in native lower-is-better form;
    fused_score is higher-is-better.
    """
    if limit <= 0 or not query.strip():
        return []
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")

    lexical_searcher = lexical_searcher or vega_query.lexical_search
    vector_searcher = vector_searcher or memory_store.search

    acc: dict[str, dict[str, Any]] = {}
    for row, rank in _collect_lexical(query, lexical_tables, lexical_top_k, lexical_searcher):
        _merge_lexical(acc, row, rank)

    for rank, row in enumerate(vector_searcher(query, person_id=person_id, limit=vector_top_k), start=1):
        _merge_vector(acc, row, rank)

    fused: list[dict[str, Any]] = []
    for item in acc.values():
        category = source_category(item.get("source"))
        lexical_weight, vector_weight, explanation = source_weights(category)
        lexical_rank = item.get("lexical_rank")
        vector_rank = item.get("vector_rank")
        item.setdefault("text", "")
        item.setdefault("snippet", "")
        item.setdefault("lexical_rank", None)
        item.setdefault("lexical_score", None)
        item.setdefault("vector_rank", None)
        item.setdefault("vector_score", None)
        item["lexical_weight"] = lexical_weight
        item["vector_weight"] = vector_weight
        item["source_weight_explanation"] = explanation
        item["fused_score"] = _rrf(lexical_rank, lexical_weight, rrf_k) + _rrf(vector_rank, vector_weight, rrf_k)
        fused.append(item)

    return sorted(
        fused,
        key=lambda r: (
            -float(r["fused_score"]),
            min(v for v in (r.get("lexical_rank"), r.get("vector_rank")) if v is not None),
            str(r.get("source") or ""),
            str(r.get("id") or ""),
        ),
    )[:limit]


def hybrid_recall(query: str, person_id: str | None = None, limit: int = 5) -> str:
    rows = hybrid_search(query, person_id=person_id, limit=limit)
    if not rows:
        return ""
    lines = [f"### Related memories ({len(rows)} found; hybrid RRF)"]
    for row in rows:
        score = float(row["fused_score"])
        lines.append(f"- ({row['source']} rrf={score:.4f}) {str(row.get('text') or '')[:200]}")
    return "\n".join(lines)
