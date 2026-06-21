# Created: 2026-05-27
# Purpose: LanceDB vector memory — per-person embedding upsert/search/recall
# Dependencies: lancedb, pipeline/data_paths.py
# Test Status: under verification

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_EMBED_MODEL = "BAAI/bge-m3"
_EMBED_DIM_BY_MODEL = {
    "BAAI/bge-m3": 1024,
}
_TABLE_NAME = "memories"


class EmbeddingConfigurationError(RuntimeError):
    """Raised when memory embedding configuration is unsupported or inconsistent."""


class EmbeddingModelUnavailableError(RuntimeError):
    """Raised when the configured real embedding model cannot be used."""


def _configured_embed_model() -> str:
    model = os.getenv("VEGA_MEMORY_EMBED_MODEL", _DEFAULT_EMBED_MODEL).strip()
    if model not in _EMBED_DIM_BY_MODEL:
        supported = ", ".join(sorted(_EMBED_DIM_BY_MODEL))
        raise EmbeddingConfigurationError(
            f"Unsupported VEGA_MEMORY_EMBED_MODEL={model!r}. Supported models with known dimensions: {supported}."
        )
    return model


_EMBED_MODEL = _configured_embed_model()
_EMBED_DIM = _EMBED_DIM_BY_MODEL[_EMBED_MODEL]


def _lance_dir() -> Path:
    try:
        from pipeline.data_paths import data_dir
        d = data_dir() / "lancedb"
    except Exception:
        d = Path.home() / "Library/Application Support/VEGA/lancedb"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_db():
    import lancedb
    return lancedb.connect(str(_lance_dir()))


def _schema():
    import pyarrow as pa
    return pa.schema([
        pa.field("id", pa.string()),
        pa.field("person_id", pa.string()),
        pa.field("source", pa.string()),
        pa.field("text", pa.string()),
        pa.field("timestamp", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), _EMBED_DIM)),
    ])


def _ensure_table():
    db = _get_db()
    if _TABLE_NAME not in db.table_names():
        import pyarrow as pa
        db.create_table(_TABLE_NAME, schema=_schema())
    return db.open_table(_TABLE_NAME)


# ── Embedding generation ───────────────────────────────────────────────────────

_embedder = None


def _get_embedder():
    """Benchmark-selected BGE-M3 embedder (lazy in-process load)."""
    global _embedder
    if _embedder is not None:
        return _embedder

    errors: list[str] = []

    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(_EMBED_MODEL)
        _embedder = ("sentence-transformers", model)
        return _embedder
    except Exception as exc:
        errors.append(f"sentence-transformers: {exc}")

    try:
        from fastembed import TextEmbedding

        model = TextEmbedding(model_name=_EMBED_MODEL)
        _embedder = ("fastembed", model)
        return _embedder
    except Exception as exc:
        errors.append(f"fastembed: {exc}")

    raise EmbeddingModelUnavailableError(
        f"Embedding model unavailable: {_EMBED_MODEL} ({_EMBED_DIM} dims). "
        "Install a supported backend and ensure the model can be loaded. "
        f"Backend errors: {'; '.join(errors)}"
    )


def _normalize_vector(vec: Any) -> list[float]:
    if hasattr(vec, "tolist"):
        vec = vec.tolist()

    arr = np.asarray(vec, dtype=np.float32)
    if arr.ndim != 1:
        arr = arr.reshape(-1)
    if arr.shape[0] != _EMBED_DIM:
        raise EmbeddingModelUnavailableError(
            f"Embedding model {_EMBED_MODEL} returned {arr.shape[0]} dims; expected {_EMBED_DIM}"
        )

    norm = float(np.linalg.norm(arr))
    if not np.isfinite(norm) or norm <= 0.0:
        raise EmbeddingModelUnavailableError(
            f"Embedding model {_EMBED_MODEL} returned an invalid zero/non-finite vector"
        )
    return (arr / norm).astype(np.float32).tolist()


def embed(text: str) -> list[float]:
    """Convert text to a normalized real BGE-M3 vector (dim=1024)."""
    backend, model = _get_embedder()
    try:
        if backend == "sentence-transformers":
            vec = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
            return _normalize_vector(vec)
        if backend == "fastembed":
            vec = next(iter(model.embed([text])))
            return _normalize_vector(vec)
    except EmbeddingModelUnavailableError:
        raise
    except Exception as exc:
        raise EmbeddingModelUnavailableError(
            f"Embedding model {_EMBED_MODEL} failed during inference via {backend}: {exc}"
        ) from exc

    raise EmbeddingModelUnavailableError(f"Unsupported embedding backend: {backend}")


# ── Public interface ───────────────────────────────────────────────────────────

def upsert(
    text: str,
    person_id: str = "default",
    source: str = "chat",
    doc_id: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Upsert a memory fragment. Generates doc_id from text hash if not provided. Returns the id."""
    if doc_id is None:
        doc_id = hashlib.md5(f"{person_id}:{text}".encode()).hexdigest()
    ts = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")
    vec = embed(text)

    import pyarrow as pa
    tbl = _ensure_table()
    row = {
        "id": doc_id,
        "person_id": person_id,
        "source": source,
        "text": text,
        "timestamp": ts,
        "vector": vec,
    }
    # upsert: merge_insert on id
    try:
        tbl.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute(
            pa.table({k: [v] for k, v in row.items()},
                     schema=_schema())
        )
    except Exception:
        # fallback: add (allows duplicates)
        tbl.add([row])
    return doc_id


def search(
    query: str,
    person_id: str | None = None,
    limit: int = 10,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Similarity search. Optionally filter by person_id."""
    vec = embed(query)
    tbl = _ensure_table()
    q = tbl.search(vec).limit(limit)
    if person_id:
        q = q.where(f"person_id = '{person_id}'", prefilter=True)
    if source:
        q = q.where(f"source = '{source}'", prefilter=True)
    rows = q.to_list()
    return [
        {
            "id": r["id"],
            "person_id": r["person_id"],
            "source": r["source"],
            "text": r["text"],
            "timestamp": r["timestamp"],
            "score": float(r.get("_distance", 0)),
        }
        for r in rows
    ]


def recall(query: str, person_id: str | None = None, limit: int = 5) -> str:
    """Format search results as prompt-ready text."""
    results = search(query, person_id=person_id, limit=limit)
    if not results:
        return ""
    lines = [f"### Related memories ({len(results)} found)"]
    for r in results:
        ts = r["timestamp"][:10] if r["timestamp"] else "?"
        lines.append(f"- [{ts}] ({r['source']}) {r['text'][:200]}")
    return "\n".join(lines)
