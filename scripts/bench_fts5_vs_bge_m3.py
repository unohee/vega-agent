#!/usr/bin/env python3
# Created: 2026-06-20
# Purpose: Reproducible local benchmark: SQLite FTS5-prefix/BM25 vs BGE-M3 top-5 memory retrieval.

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import sqlite3
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.data_paths import data_dir, db_path

TOP_K = 5
MODEL_NAME = "BAAI/bge-m3"
QUERIES: list[str] = [
    "user preferences and personal background",
    "current project goals and deadlines",
    "recent bugs errors failures to fix",
    "database schema migrations sqlite tables",
    "authentication oauth login token issues",
    "email calendar meeting follow up reminders",
    "finance automation reconciliation reports",
    "agent tools sandbox shell execution",
    "frontend dashboard ui layout problems",
    "vector memory retrieval embeddings search",
    "deployment release signing notarization update",
    "contacts people organizations relationship notes",
    "daily brief status progress blockers",
    "todo task priority next action",
    "API integration webhook request response",
]
TABLE_PRIORITY = (
    "memories",
    "memory",
    "user_memories",
    "agent_memories",
    "notes",
    "messages",
    "message",
    "sessions",
    "conversation_messages",
    "chat_messages",
)
TEXT_COLUMNS = (
    "text",
    "content",
    "message",
    "body",
    "summary",
    "title",
    "name",
    "description",
    "transcript",
)
METADATA_COLUMNS = (
    "person_id",
    "source",
    "timestamp",
    "created_at",
    "updated_at",
    "role",
    "session_id",
    "conversation_id",
    "type",
    "kind",
)


@dataclass(frozen=True)
class CorpusRow:
    id: str
    text: str
    metadata: dict[str, Any]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact_error(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)}


def snippet(text: str, limit: int = 240) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    return str(value)


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def columns_for(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return list(conn.execute(f"PRAGMA table_info({quote_ident(table)})"))


def choose_text_columns(columns: list[sqlite3.Row]) -> list[str]:
    names = [str(col[1]) for col in columns]
    lowered = {name.lower(): name for name in names}
    preferred = [lowered[name] for name in TEXT_COLUMNS if name in lowered]
    if preferred:
        return preferred
    fallback: list[str] = []
    for col in columns:
        name = str(col[1])
        declared_type = str(col[2] or "").upper()
        if "TEXT" in declared_type or "CHAR" in declared_type or "CLOB" in declared_type:
            fallback.append(name)
    return fallback[:3]


def choose_id_column(columns: list[sqlite3.Row]) -> str | None:
    names = [str(col[1]) for col in columns]
    lowered = {name.lower(): name for name in names}
    for wanted in ("id", "uuid", "doc_id", "message_id", "canonical_id"):
        if wanted in lowered:
            return lowered[wanted]
    for col in columns:
        if int(col[5] or 0) > 0:
            return str(col[1])
    return None


def candidate_tables(conn: sqlite3.Connection) -> list[tuple[str, list[str], str | None, list[str]]]:
    found: list[tuple[str, list[str], str | None, list[str]]] = []
    for table in table_names(conn):
        if table.endswith(("_fts", "_fts_data", "_fts_idx", "_fts_docsize", "_fts_config")):
            continue
        columns = columns_for(conn, table)
        text_cols = choose_text_columns(columns)
        if not text_cols:
            continue
        names = [str(col[1]) for col in columns]
        lowered = {name.lower(): name for name in names}
        metadata_cols = [lowered[name] for name in METADATA_COLUMNS if name in lowered and lowered[name] not in text_cols]
        found.append((table, text_cols, choose_id_column(columns), metadata_cols))
    priority = {name: idx for idx, name in enumerate(TABLE_PRIORITY)}
    found.sort(key=lambda item: (priority.get(item[0].lower(), 10_000), item[0].lower()))
    return found


def load_sqlite_corpus(sqlite_path: Path, max_rows_per_table: int) -> tuple[list[CorpusRow], dict[str, Any]]:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {sqlite_path}")
    if not os.access(sqlite_path, os.R_OK):
        raise PermissionError(f"SQLite DB is not readable: {sqlite_path}")

    corpus: list[CorpusRow] = []
    seen: set[str] = set()
    diagnostics: dict[str, Any] = {"tables_considered": [], "tables_used": []}
    with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for table, text_cols, id_col, metadata_cols in candidate_tables(conn):
            diagnostics["tables_considered"].append(
                {"table": table, "text_columns": text_cols, "id_column": id_col, "metadata_columns": metadata_cols}
            )
            projected = ["rowid AS __rowid"]
            aliases: dict[str, str] = {}
            for col in dict.fromkeys(([id_col] if id_col else []) + text_cols + metadata_cols):
                if not col:
                    continue
                alias = f"__col_{len(aliases)}"
                aliases[col] = alias
                projected.append(f"{quote_ident(col)} AS {quote_ident(alias)}")
            sql = f"SELECT {', '.join(projected)} FROM {quote_ident(table)} LIMIT ?"
            used = 0
            for row in conn.execute(sql, (max_rows_per_table,)):
                parts = []
                for col in text_cols:
                    value = row[aliases[col]]
                    if value is not None and str(value).strip():
                        parts.append(str(value).strip())
                text = "\n".join(parts).strip()
                if not text:
                    continue
                row_id_value = row[aliases[id_col]] if id_col else row["__rowid"]
                cid = f"sqlite:{table}:{row_id_value}"
                if cid in seen:
                    continue
                seen.add(cid)
                metadata = {
                    "source": "sqlite",
                    "sqlite_table": table,
                    "sqlite_rowid": row["__rowid"],
                    "sqlite_id_column": id_col,
                    "sqlite_id_value": jsonable(row_id_value),
                    "text_columns": text_cols,
                }
                for col in metadata_cols:
                    metadata[col] = jsonable(row[aliases[col]])
                corpus.append(CorpusRow(id=cid, text=text, metadata=metadata))
                used += 1
            if used:
                diagnostics["tables_used"].append({"table": table, "row_count": used})
    if not corpus:
        raise RuntimeError(f"No readable text rows found in SQLite DB: {sqlite_path}")
    diagnostics["row_count"] = len(corpus)
    return corpus, diagnostics


def load_lancedb_corpus(lance_dir: Path) -> tuple[list[CorpusRow], dict[str, Any]]:
    info: dict[str, Any] = {"path": str(lance_dir), "available": importlib.util.find_spec("lancedb") is not None}
    if not info["available"]:
        info["note"] = "lancedb package not installed"
        return [], info
    if not lance_dir.exists():
        info["note"] = "LanceDB directory absent"
        return [], info
    try:
        import lancedb

        db = lancedb.connect(str(lance_dir))
        tables = list(db.table_names())
        info["tables"] = tables
        if "memories" not in tables:
            info["memories_table"] = {"present": False}
            return [], info
        table = db.open_table("memories")
        try:
            info["memories_table"] = {"present": True, "count": int(table.count_rows())}
        except Exception as exc:  # diagnostic only; table reading below can still work
            info["memories_table"] = {"present": True, "count_error": compact_error(exc)}
        rows = table.to_arrow().to_pylist()
    except Exception as exc:
        info["error"] = compact_error(exc)
        return [], info

    corpus: list[CorpusRow] = []
    for idx, row in enumerate(rows):
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        raw_id = row.get("id") or idx
        metadata = {key: jsonable(value) for key, value in row.items() if key != "vector"}
        metadata["source"] = "lancedb"
        corpus.append(CorpusRow(id=f"lancedb:memories:{raw_id}", text=text, metadata=metadata))
    info["loaded_text_rows"] = len(corpus)
    return corpus, info


def merge_corpus(sqlite_rows: list[CorpusRow], lancedb_rows: list[CorpusRow]) -> list[CorpusRow]:
    merged: list[CorpusRow] = []
    seen_ids: set[str] = set()
    seen_texts: set[str] = set()
    for row in sqlite_rows + lancedb_rows:
        text_key = re.sub(r"\s+", " ", row.text).strip().lower()
        if row.id in seen_ids or text_key in seen_texts:
            continue
        seen_ids.add(row.id)
        seen_texts.add(text_key)
        merged.append(row)
    return merged


def tokenize_prefixes(query: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[\w]+", query, flags=re.UNICODE) if len(token) >= 2]


def fts_match_expr(tokens: list[str]) -> str:
    return " OR ".join(f'"{token}"*' for token in tokens)


def build_fts_index(corpus: list[CorpusRow]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE VIRTUAL TABLE bench_fts USING fts5(id UNINDEXED, text, metadata UNINDEXED)")
    conn.executemany(
        "INSERT INTO bench_fts(id, text, metadata) VALUES (?, ?, ?)",
        ((row.id, row.text, json.dumps(row.metadata, ensure_ascii=False, sort_keys=True)) for row in corpus),
    )
    return conn


def run_fts_query(conn: sqlite3.Connection, query: str) -> dict[str, Any]:
    tokens = tokenize_prefixes(query)
    match_expr = fts_match_expr(tokens)
    started = time.perf_counter()
    rows: list[sqlite3.Row] = []
    error: dict[str, str] | None = None
    if match_expr:
        try:
            rows = list(
                conn.execute(
                    "SELECT id, text, metadata, bm25(bench_fts) AS bm25 "
                    "FROM bench_fts WHERE bench_fts MATCH ? ORDER BY bm25 LIMIT ?",
                    (match_expr, TOP_K),
                )
            )
        except sqlite3.Error as exc:
            error = compact_error(exc)
    latency_ms = (time.perf_counter() - started) * 1000.0
    candidates = [
        {
            "rank": rank,
            "id": row["id"],
            "snippet": snippet(row["text"]),
            "metadata": json.loads(row["metadata"]),
            "score": {"bm25": float(row["bm25"]), "ordering": "ascending_lower_is_better"},
        }
        for rank, row in enumerate(rows, 1)
    ]
    out: dict[str, Any] = {
        "method": "sqlite_fts5_prefix_bm25",
        "latency_ms": latency_ms,
        "scoring_inputs": {"tokens": tokens, "match_expression": match_expr, "top_k": TOP_K},
        "candidates": candidates,
    }
    if error:
        out["error"] = error
    return out


def detect_backends() -> dict[str, Any]:
    notes: dict[str, Any] = {
        "sentence_transformers": {"available": importlib.util.find_spec("sentence_transformers") is not None},
        "fastembed": {"available": importlib.util.find_spec("fastembed") is not None},
        "mlx": {"available": importlib.util.find_spec("mlx") is not None or importlib.util.find_spec("mlx_lm") is not None},
    }
    torch_spec = importlib.util.find_spec("torch")
    torch_note: dict[str, Any] = {"available": torch_spec is not None}
    if torch_spec is not None:
        try:
            import torch

            torch_note.update(
                {
                    "mps_available": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()),
                    "cuda_available": bool(torch.cuda.is_available()),
                }
            )
        except Exception as exc:
            torch_note["error"] = compact_error(exc)
    notes["torch"] = torch_note
    if notes["fastembed"]["available"]:
        notes["fastembed"]["note"] = "detected only; benchmark uses BAAI/bge-m3 through sentence-transformers unless explicitly implemented here"
    if notes["mlx"]["available"]:
        notes["mlx"]["note"] = "detected only; no MLX BGE-M3 path is executed by this script"
    return notes


def select_device(backend_notes: dict[str, Any], requested: str) -> str:
    if requested != "auto":
        return requested
    torch_note = backend_notes.get("torch", {})
    if torch_note.get("mps_available"):
        return "mps"
    if torch_note.get("cuda_available"):
        return "cuda"
    return "cpu"


def normalize_vector(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 0.0:
        return values
    return [v / norm for v in values]


def dot(left: list[float], right: list[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right)))


class BgeM3Index:
    def __init__(self, corpus: list[CorpusRow], device: str):
        if importlib.util.find_spec("sentence_transformers") is None:
            raise RuntimeError("sentence-transformers is not installed; cannot compute real BAAI/bge-m3 embeddings")
        from sentence_transformers import SentenceTransformer

        self.corpus = corpus
        self.device = device
        started = time.perf_counter()
        self.model = SentenceTransformer(MODEL_NAME, device=device)
        self.model_load_ms = (time.perf_counter() - started) * 1000.0
        started = time.perf_counter()
        embeddings = self.model.encode(
            [row.text for row in corpus],
            batch_size=16,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        self.embedding_ms = (time.perf_counter() - started) * 1000.0
        self.vectors = [normalize_vector(vec) for vec in embeddings]
        self.dimension = len(self.vectors[0]) if self.vectors else 0

    def search(self, query: str) -> dict[str, Any]:
        started = time.perf_counter()
        query_vector = normalize_vector(
            self.model.encode([query], convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)[0]
        )
        scored = [(idx, dot(query_vector, vec)) for idx, vec in enumerate(self.vectors)]
        scored.sort(key=lambda item: item[1], reverse=True)
        latency_ms = (time.perf_counter() - started) * 1000.0
        candidates = []
        for rank, (idx, similarity) in enumerate(scored[:TOP_K], 1):
            row = self.corpus[idx]
            candidates.append(
                {
                    "rank": rank,
                    "id": row.id,
                    "snippet": snippet(row.text),
                    "metadata": row.metadata,
                    "score": {
                        "cosine_similarity": float(similarity),
                        "cosine_distance": float(1.0 - similarity),
                        "ordering": "descending_similarity_higher_is_better",
                    },
                }
            )
        return {
            "method": "bge_m3_sentence_transformers_in_process_cosine",
            "latency_ms": latency_ms,
            "scoring_inputs": {
                "model": MODEL_NAME,
                "device": self.device,
                "top_k": TOP_K,
                "normalized_embeddings": True,
                "query_embedding_dim": len(query_vector),
                "corpus_embedding_dim": self.dimension,
            },
            "candidates": candidates,
        }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark real local memory retrieval: SQLite FTS5 prefix/BM25 vs BGE-M3 top-5 semantic search."
    )
    parser.add_argument("--output", "-o", type=Path, help="Optional JSON output path. Stdout remains default.")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout when --output is provided.")
    parser.add_argument("--max-rows-per-table", type=int, default=25_000, help="Safety cap per SQLite source table.")
    parser.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda", "mps"), help="sentence-transformers device.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    started = time.perf_counter()
    root_data_dir = data_dir()
    sqlite_path = db_path()
    lance_dir = root_data_dir / "lancedb"
    backend_notes = detect_backends()
    device = select_device(backend_notes, args.device)
    errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    run: dict[str, Any] = {
        "created_at": now_iso(),
        "benchmark": "fts5_prefix_vs_bge_m3_top5",
        "repo_root": str(ROOT),
        "paths": {"data_dir": str(root_data_dir), "sqlite_db": str(sqlite_path), "lancedb_dir": str(lance_dir)},
        "queries": QUERIES,
        "query_count": len(QUERIES),
        "top_k": TOP_K,
        "backend_notes": backend_notes,
        "model_notes": {"semantic_model": MODEL_NAME, "requested_device": args.device, "selected_device": device},
        "warnings": warnings,
        "errors": errors,
        "results": [],
    }

    exit_code = 0
    try:
        sqlite_rows, sqlite_diag = load_sqlite_corpus(sqlite_path, args.max_rows_per_table)
        lancedb_rows, lancedb_diag = load_lancedb_corpus(lance_dir)
        corpus = merge_corpus(sqlite_rows, lancedb_rows)
        if not corpus:
            raise RuntimeError("No real local memory corpus rows were loaded from SQLite/LanceDB")
        run["corpus"] = {
            "row_count": len(corpus),
            "sqlite": sqlite_diag,
            "lancedb": lancedb_diag,
            "sources": {"sqlite_rows": len(sqlite_rows), "lancedb_text_rows": len(lancedb_rows)},
        }

        fts_conn = build_fts_index(corpus)
        bge_index: BgeM3Index | None = None
        bge_error: dict[str, str] | None = None
        try:
            bge_index = BgeM3Index(corpus, device=device)
            run["model_notes"].update(
                {
                    "sentence_transformers_model_load_ms": bge_index.model_load_ms,
                    "corpus_embedding_ms": bge_index.embedding_ms,
                    "embedding_dim": bge_index.dimension,
                    "semantic_index": "in_process_cosine_over_real_bge_m3_embeddings",
                }
            )
        except Exception as exc:
            bge_error = compact_error(exc)
            errors.append({"error": bge_error, "stage": "bge_m3_index_build"})
            exit_code = 1

        for query in QUERIES:
            semantic_result: dict[str, Any]
            if bge_index is None:
                semantic_result = {
                    "method": "bge_m3_sentence_transformers_in_process_cosine",
                    "latency_ms": None,
                    "scoring_inputs": {"model": MODEL_NAME, "device": device, "top_k": TOP_K},
                    "candidates": [],
                    "error": bge_error,
                }
            else:
                semantic_result = bge_index.search(query)
            run["results"].append({"query": query, "fts5": run_fts_query(fts_conn, query), "bge_m3": semantic_result})
    except Exception as exc:
        errors.append({"error": compact_error(exc), "traceback": traceback.format_exc(limit=8)})
        exit_code = 1
    finally:
        run["total_latency_ms"] = (time.perf_counter() - started) * 1000.0

    text = json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    if not args.quiet or not args.output:
        print(text)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
