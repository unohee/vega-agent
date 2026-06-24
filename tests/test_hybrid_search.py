# Created: 2026-06-21
# Purpose: Weighted RRF hybrid search unit coverage.
# Dependencies: pipeline.hybrid_search
# Test Status: pytest tests/test_hybrid_search.py

from __future__ import annotations

from typing import Any

from pipeline.hybrid_search import hybrid_search


def test_hybrid_search_surfaces_semantic_paraphrase_in_top5() -> None:
    def lexical_searcher(table: str, query: str, top_k: int) -> list[dict[str, Any]]:
        return []

    def vector_searcher(query: str, person_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        assert query == "트레이딩"
        return [
            {
                "id": "m-stock-trade",
                "person_id": person_id or "default",
                "source": "chat",
                "text": "주식 매매 원칙: 손절과 포지션 크기를 먼저 정한다.",
                "timestamp": "2026-06-01T00:00:00Z",
                "score": 0.12,
            }
        ]

    rows = hybrid_search(
        "트레이딩",
        limit=5,
        lexical_searcher=lexical_searcher,
        vector_searcher=vector_searcher,
    )

    assert rows[0]["id"] == "m-stock-trade"
    assert rows[0]["source"] == "messages"
    assert rows[0]["lexical_rank"] is None
    assert rows[0]["vector_rank"] == 1
    assert rows[0]["vector_score"] == 0.12
    assert rows[0]["fused_score"] > 0
    assert "balanced" in rows[0]["source_weight_explanation"]


def test_hybrid_search_surfaces_proper_noun_ticker_with_lexical_entity_weight() -> None:
    def lexical_searcher(table: str, query: str, top_k: int) -> list[dict[str, Any]]:
        if table == "entities":
            return [
                {
                    "source": "entities_fts",
                    "table": "entities",
                    "id": 7,
                    "text": "Apple Inc ticker AAPL aliases 애플",
                    "snippet": "Apple Inc ticker <mark>AAPL</mark>",
                    "bm25": -3.2,
                }
            ]
        return []

    def vector_searcher(query: str, person_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "id": "generic-1",
                "person_id": "default",
                "source": "chat",
                "text": "일반 시장 뉴스 요약",
                "timestamp": "2026-06-02T00:00:00Z",
                "score": 0.05,
            }
        ]

    rows = hybrid_search(
        "AAPL",
        limit=5,
        lexical_searcher=lexical_searcher,
        vector_searcher=vector_searcher,
    )

    assert [row["id"] for row in rows][:2] == [7, "generic-1"]
    entity = rows[0]
    assert entity["source"] == "entities"
    assert entity["lexical_rank"] == 1
    assert entity["lexical_score"] == -3.2
    assert entity["vector_rank"] is None
    assert entity["lexical_weight"] > entity["vector_weight"]
    assert "favor lexical" in entity["source_weight_explanation"]


def test_hybrid_search_deduplicates_by_stable_source_id_and_keeps_rank_metadata() -> None:
    def lexical_searcher(table: str, query: str, top_k: int) -> list[dict[str, Any]]:
        if table == "messages":
            return [
                {
                    "source": "messages_fts",
                    "table": "messages",
                    "id": "same-id",
                    "text": "NVDA 실적 발표 메모",
                    "snippet": "<mark>NVDA</mark> 실적 발표 메모",
                    "bm25": -1.0,
                }
            ]
        return []

    def vector_searcher(query: str, person_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "id": "same-id",
                "person_id": "default",
                "source": "chat",
                "text": "NVDA 실적 발표 메모",
                "timestamp": "2026-06-03T00:00:00Z",
                "score": 0.08,
            }
        ]

    rows = hybrid_search(
        "NVDA",
        limit=5,
        lexical_searcher=lexical_searcher,
        vector_searcher=vector_searcher,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["stable_key"] == "messages:same-id"
    assert row["lexical_rank"] == 1
    assert row["lexical_score"] == -1.0
    assert row["vector_rank"] == 1
    assert row["vector_score"] == 0.08
    assert row["fused_score"] > 0
    assert row["source_weight_explanation"]


def test_hybrid_search_promotes_only_strong_vector_hit_through_lexical_noise() -> None:
    def lexical_searcher(table: str, query: str, top_k: int) -> list[dict[str, Any]]:
        if table != "entities":
            return []
        return [
            {
                "source": "entities_fts",
                "table": "entities",
                "id": idx,
                "text": f"broad lexical match {idx}",
                "snippet": f"broad lexical match {idx}",
                "bm25": -float(idx),
            }
            for idx in range(1, 9)
        ]

    def vector_searcher(query: str, person_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "id": "semantic-memory",
                "person_id": "default",
                "source": "chat",
                "text": "KYTE 음악 라이선스 AI 인프라 담당 메모",
                "timestamp": "2026-06-21T00:00:00Z",
                "score": 0.36,
            }
        ]

    rows = hybrid_search(
        "KYTE 음악 라이선스 AI 인프라 담당자",
        limit=5,
        lexical_searcher=lexical_searcher,
        vector_searcher=vector_searcher,
    )

    assert rows[0]["id"] == "semantic-memory"
    assert rows[0]["vector_confidence_bonus"] > 0
    assert "semantic-memory" in [row["id"] for row in rows[:5]]


def test_hybrid_search_does_not_promote_weak_vector_hit_over_exact_entity() -> None:
    def lexical_searcher(table: str, query: str, top_k: int) -> list[dict[str, Any]]:
        if table == "entities":
            return [
                {
                    "source": "entities_fts",
                    "table": "entities",
                    "id": "kyte-ax",
                    "text": "KYTE AX project",
                    "snippet": "<mark>KYTE</mark> <mark>AX</mark> project",
                    "bm25": -4.0,
                }
            ]
        return []

    def vector_searcher(query: str, person_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "id": "weak-semantic-memory",
                "person_id": "default",
                "source": "chat",
                "text": "unrelated memory",
                "timestamp": "2026-06-21T00:00:00Z",
                "score": 1.20,
            }
        ]

    rows = hybrid_search(
        "KYTE AX",
        limit=5,
        lexical_searcher=lexical_searcher,
        vector_searcher=vector_searcher,
    )

    assert rows[0]["id"] == "kyte-ax"
    assert rows[1]["id"] == "weak-semantic-memory"
    assert rows[1]["vector_confidence_bonus"] == 0.0
