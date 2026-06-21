from __future__ import annotations

from typing import Any

from pipeline.vega_query import lexical_search_messages


def memory_search(query: str, top_k: int = 5) -> dict[str, Any]:
    """Perform ranked lexical search over message history using FTS5.

    Args:
        query: Search query string.
        top_k: Maximum number of results to return.

    Returns:
        Dictionary with 'results' key containing list of search hits.
    """
    results = lexical_search_messages(query, top_k)
    return {"results": results}
