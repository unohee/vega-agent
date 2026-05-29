# Created: 2026-05-27
# Purpose: LanceDB vector memory — per-person embedding upsert/search/recall
# Dependencies: lancedb, pipeline/data_paths.py
# Test Status: under verification

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_EMBED_DIM = 256  # EXAONE-3-1.2B hidden size (fallback: deterministic hash 256-dim)
_TABLE_NAME = "memories"


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
    """EXAONE-3-1.2B MLX embedding model (lazy load)."""
    global _embedder
    if _embedder is not None:
        return _embedder
    try:
        from mlx_lm import load
        model, tokenizer = load("LGAI-EXAONE/EXAONE-3-1.2B-Instruct")
        _embedder = (model, tokenizer)
        return _embedder
    except Exception as e:
        logger.warning("EXAONE embedding load failed, using hash fallback: %s", e)
        return None


def embed(text: str) -> list[float]:
    """Convert text to a float32 vector (dim=256). Falls back to deterministic hash if model is unavailable."""
    embedder = _get_embedder()
    if embedder is not None:
        try:
            import mlx.core as mx
            model, tokenizer = embedder
            tokens = tokenizer(text, return_tensors="mlx", truncation=True, max_length=512)
            out = model(**tokens)
            # mean pool last hidden state → slice to 256-dim
            hidden = out.last_hidden_state[0]  # (seq, hidden)
            vec = mx.mean(hidden, axis=0).tolist()
            # slice to 256-dim or pad if shorter
            if len(vec) >= _EMBED_DIM:
                vec = vec[:_EMBED_DIM]
            else:
                vec = vec + [0.0] * (_EMBED_DIM - len(vec))
            norm = (sum(v * v for v in vec) ** 0.5) or 1.0
            return [v / norm for v in vec]
        except Exception as e:
            logger.debug("Embedding failed, falling back: %s", e)

    # deterministic hash fallback: SHA256 → 256 floats in [-1, 1]
    h = hashlib.sha256(text.encode()).digest()
    vec = [(b / 127.5) - 1.0 for b in h]  # 32 bytes → tile to 256
    vec = (vec * 8)[:_EMBED_DIM]
    norm = (sum(v * v for v in vec) ** 0.5) or 1.0
    return [v / norm for v in vec]


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
