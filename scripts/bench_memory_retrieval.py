#!/usr/bin/env python3
"""Reproducible local memory retrieval benchmark.

Runs fixed user-memory queries against existing local storage only:
- SQLite FTS5 prefix MATCH + bm25 top-5
- LanceDB semantic top-5 using sentence-transformers BGE-M3 embeddings

No schema/table/data is created by this script.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOP_K = 5
BGE_M3_MODEL = "BAAI/bge-m3"
MEMORY_TABLE = "memories"
QUERIES = [
    "user preferred working style and communication tone",
    "important personal preferences the assistant should remember",
    "current active projects and project goals",
    "recent decisions about product architecture",
    "open tasks blockers and follow up items",
    "calendar events meetings deadlines travel plans",
    "people contacts companies and relationship context",
    "finance automation accounting tax or invoice preferences",
    "investment portfolio risk constraints trading notes",
    "software stack repository deployment and infrastructure details",
    "bugs incidents regressions and troubleshooting history",
    "documents notes summaries and research findings",
]

TOKEN_RE = re.compile(r"[\w가-힣]{2,}", re.UNICODE)


class BenchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FtsTable:
    name: str
    text_columns: list[str]
    id_column: str | None
    row_count: int
    sql: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def import_probe(module_name: str) -> dict[str, Any]:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return {"available": False, "reason": "module_not_found"}
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # import side-effect failures are diagnostics, not benchmark failures
        return {"available": False, "reason": f"import_failed: {type(exc).__name__}: {exc}"}
    return {"available": True, "version": getattr(module, "__version__", None)}


def resolve_paths() -> dict[str, Path]:
    from pipeline.data_paths import data_dir, db_path

    # Match pipeline.memory_store._lance_dir() without calling it; that helper creates
    # the directory, while this benchmark must validate existing storage only.
    return {"sqlite_db": db_path(), "lancedb_dir": data_dir() / "lancedb"}


def connect_existing_sqlite(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise BenchError(f"SQLite DB is missing: {db_path}")
    if db_path.stat().st_size == 0:
        raise BenchError(f"SQLite DB is empty: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def visible_columns(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    rows = conn.execute(f"PRAGMA table_xinfo({quote_ident(table)})").fetchall()
    return [r for r in rows if int(r[6] or 0) == 0]


def discover_fts5_table(conn: sqlite3.Connection) -> FtsTable:
    rows = conn.execute(
        """
        SELECT name, sql FROM sqlite_schema
        WHERE type='table'
          AND sql IS NOT NULL
          AND lower(sql) LIKE '%using fts5%'
        ORDER BY name
        """
    ).fetchall()
    candidates: list[FtsTable] = []
    for row in rows:
        name = row["name"]
        sql = row["sql"] or ""
        cols = visible_columns(conn, name)
        text_columns = [r[1] for r in cols if str(r[2] or "").upper() in {"", "TEXT", "VARCHAR", "CHAR"}]
        if not text_columns:
            text_columns = [r[1] for r in cols]
        id_column = next((c for c in ("id", "doc_id", "uuid", "message_id", "rowid") if c in {r[1] for r in cols}), None)
        try:
            row_count = int(conn.execute(f"SELECT count(*) FROM {quote_ident(name)}").fetchone()[0])
        except sqlite3.Error:
            continue
        if row_count > 0 and text_columns:
            candidates.append(FtsTable(name=name, text_columns=text_columns, id_column=id_column, row_count=row_count, sql=sql))
    if not candidates:
        raise BenchError("No non-empty SQLite FTS5 table with usable text columns found in existing DB")
    return sorted(candidates, key=lambda c: (-c.row_count, c.name))[0]


def make_prefix_match(query: str) -> str:
    tokens = TOKEN_RE.findall(query.lower())
    if not tokens:
        raise BenchError(f"Query produced no usable FTS tokens: {query!r}")
    return " OR ".join(f'"{token}"*' for token in tokens[:8])


def run_fts_query(conn: sqlite3.Connection, table: FtsTable, query: str) -> dict[str, Any]:
    match = make_prefix_match(query)
    text_expr = " || ' ' || ".join(f"COALESCE({quote_ident(c)}, '')" for c in table.text_columns)
    id_expr = quote_ident(table.id_column) if table.id_column and table.id_column != "rowid" else "rowid"
    sql = f"""
        SELECT {id_expr} AS doc_id,
               rowid AS rowid,
               {text_expr} AS text,
               bm25({quote_ident(table.name)}) AS score
        FROM {quote_ident(table.name)}
        WHERE {quote_ident(table.name)} MATCH ?
        ORDER BY score ASC
        LIMIT ?
    """
    start = time.perf_counter()
    rows = conn.execute(sql, (match, TOP_K)).fetchall()
    latency = elapsed_ms(start)
    candidates = []
    for rank, row in enumerate(rows, start=1):
        text = row["text"] or ""
        candidates.append(
            {
                "rank": rank,
                "id": str(row["doc_id"]),
                "rowid": row["rowid"],
                "text": text,
                "snippet": text[:300],
                "score": float(row["score"]),
            }
        )
    return {"latency_ms": latency, "match_query": match, "candidates": candidates}


def device_preference() -> dict[str, Any]:
    info: dict[str, Any] = {"device": "cpu", "torch_available": False, "mps_available": False}
    try:
        import torch

        info["torch_available"] = True
        info["torch_version"] = getattr(torch, "__version__", None)
        mps_available = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        info["mps_available"] = mps_available
        info["device"] = "mps" if mps_available else "cpu"
    except Exception as exc:
        info["torch_error"] = f"{type(exc).__name__}: {exc}"
    return info


def load_bge_model(device: str):
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise BenchError(f"sentence-transformers is required for BGE-M3 semantic benchmark: {type(exc).__name__}: {exc}") from exc
    start = time.perf_counter()
    model = SentenceTransformer(BGE_M3_MODEL, device=device)
    return model, elapsed_ms(start)


def encode_query(model: Any, query: str) -> list[float]:
    vector = model.encode(query, normalize_embeddings=True)
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    if vector and isinstance(vector[0], list):
        vector = vector[0]
    return [float(v) for v in vector]


def lance_schema_fields(table: Any) -> dict[str, str]:
    schema = table.schema
    return {field.name: str(field.type) for field in schema}


def vector_dimension_from_schema(fields: dict[str, str]) -> int | None:
    vector_type = fields.get("vector")
    if not vector_type:
        return None
    match = re.search(r"fixed_size_list<[^>]+>\[(\d+)\]", vector_type)
    if match:
        return int(match.group(1))
    match = re.search(r"list<.*>\[(\d+)\]", vector_type)
    if match:
        return int(match.group(1))
    return None


def open_existing_lancedb(lance_dir: Path) -> tuple[Any, Any, dict[str, Any]]:
    if not lance_dir.exists():
        raise BenchError(f"LanceDB directory is missing: {lance_dir}")
    try:
        import lancedb
    except Exception as exc:
        raise BenchError(f"lancedb is required for semantic benchmark: {type(exc).__name__}: {exc}") from exc
    db = lancedb.connect(str(lance_dir))
    table_names = list(db.table_names())
    if MEMORY_TABLE not in table_names:
        raise BenchError(f"LanceDB table '{MEMORY_TABLE}' missing in {lance_dir}; tables={table_names}")
    table = db.open_table(MEMORY_TABLE)
    try:
        row_count = int(table.count_rows())
    except TypeError:
        row_count = int(table.count_rows(None))
    if row_count <= 0:
        raise BenchError(f"LanceDB table '{MEMORY_TABLE}' has no rows")
    fields = lance_schema_fields(table)
    missing = [c for c in ("id", "text", "vector") if c not in fields]
    if missing:
        raise BenchError(f"LanceDB table '{MEMORY_TABLE}' missing required columns: {missing}; fields={fields}")
    metadata = {"table": MEMORY_TABLE, "table_names": table_names, "row_count": row_count, "schema": fields}
    dim = vector_dimension_from_schema(fields)
    if dim is not None:
        metadata["vector_dimension"] = dim
    return db, table, metadata


def load_lance_memory_rows(table: Any) -> list[dict[str, Any]]:
    try:
        rows = table.to_arrow().to_pylist()
    except Exception as first_exc:
        try:
            rows = table.to_pandas().to_dict("records")
        except Exception as second_exc:
            raise BenchError(
                "Failed to read existing LanceDB memory rows: "
                f"to_arrow={type(first_exc).__name__}: {first_exc}; "
                f"to_pandas={type(second_exc).__name__}: {second_exc}"
            ) from second_exc
    docs = [row for row in rows if str(row.get("text") or "").strip()]
    if not docs:
        raise BenchError(f"LanceDB table '{MEMORY_TABLE}' has no rows with non-empty text")
    return docs


def build_bge_corpus(model: Any, table: Any) -> tuple[list[dict[str, Any]], Any, float]:
    import numpy as np

    docs = load_lance_memory_rows(table)
    texts = [str(row.get("text") or "") for row in docs]
    start = time.perf_counter()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    latency = elapsed_ms(start)
    matrix = np.asarray(embeddings, dtype="float32")
    if matrix.ndim != 2 or matrix.shape[0] != len(docs):
        raise BenchError(f"Unexpected BGE-M3 corpus embedding shape: {matrix.shape}; docs={len(docs)}")
    return docs, matrix, latency


def run_semantic_query(docs: list[dict[str, Any]], corpus_embeddings: Any, model: Any, query: str) -> dict[str, Any]:
    import numpy as np

    start = time.perf_counter()
    vector = np.asarray(encode_query(model, query), dtype="float32")
    encode_latency = elapsed_ms(start)
    search_start = time.perf_counter()
    scores = corpus_embeddings @ vector
    top_indexes = np.argsort(-scores, kind="stable")[:TOP_K]
    search_latency = elapsed_ms(search_start)
    candidates = []
    for rank, idx in enumerate(top_indexes, start=1):
        row = docs[int(idx)]
        score = float(scores[int(idx)])
        text = str(row.get("text", ""))
        candidates.append(
            {
                "rank": rank,
                "id": str(row.get("id")),
                "person_id": row.get("person_id"),
                "source": row.get("source"),
                "timestamp": row.get("timestamp"),
                "text": text,
                "snippet": text[:300],
                "distance": float(1.0 - score),
                "score": score,
            }
        )
    return {
        "latency_ms": round(encode_latency + search_latency, 3),
        "embedding_latency_ms": encode_latency,
        "search_latency_ms": search_latency,
        "candidates": candidates,
    }


def run_benchmark() -> tuple[dict[str, Any], int]:
    backend_availability = {
        "fastembed": import_probe("fastembed"),
        "mlx": import_probe("mlx"),
        "mlx_lm": import_probe("mlx_lm"),
        "sentence_transformers": import_probe("sentence_transformers"),
        "lancedb": import_probe("lancedb"),
    }
    paths = resolve_paths()
    output: dict[str, Any] = {
        "ok": False,
        "created_at": now_iso(),
        "benchmark": "memory_retrieval_fts5_prefix_vs_bge_m3",
        "top_k": TOP_K,
        "queries": [{"query_id": f"q{i:02d}", "text": q} for i, q in enumerate(QUERIES, start=1)],
        "storage": paths,
        "backend_availability": backend_availability,
        "metadata": {"bge_m3_model": BGE_M3_MODEL},
        "results": [],
        "errors": [],
    }

    try:
        with connect_existing_sqlite(paths["sqlite_db"]) as conn:
            fts_table = discover_fts5_table(conn)
            output["metadata"]["sqlite_fts5"] = {
                "table": fts_table.name,
                "text_columns": fts_table.text_columns,
                "id_column": fts_table.id_column,
                "row_count": fts_table.row_count,
                "sql": fts_table.sql,
            }
            _db, lance_table, lance_meta = open_existing_lancedb(paths["lancedb_dir"])
            output["metadata"]["lancedb"] = lance_meta
            device_info = device_preference()
            output["backend_availability"]["torch"] = device_info
            model, load_latency = load_bge_model(device_info["device"])
            output["metadata"]["bge_m3_load_latency_ms"] = load_latency
            output["metadata"]["bge_m3_device"] = device_info["device"]
            docs, corpus_embeddings, corpus_latency = build_bge_corpus(model, lance_table)
            output["metadata"]["bge_m3_corpus_embedding_latency_ms"] = corpus_latency
            output["metadata"]["bge_m3_corpus_rows"] = len(docs)
            output["metadata"]["bge_m3_embedding_dimension"] = int(corpus_embeddings.shape[1])

            for i, query in enumerate(QUERIES, start=1):
                query_id = f"q{i:02d}"
                fts_result = run_fts_query(conn, fts_table, query)
                output["results"].append(
                    {
                        "query_id": query_id,
                        "query_text": query,
                        "method": "sqlite_fts5_prefix_bm25",
                        "backend": "sqlite_fts5",
                        **fts_result,
                    }
                )
                semantic_result = run_semantic_query(docs, corpus_embeddings, model, query)
                output["results"].append(
                    {
                        "query_id": query_id,
                        "query_text": query,
                        "method": "semantic_bge_m3",
                        "backend": "sentence_transformers",
                        "model": BGE_M3_MODEL,
                        "device": device_info["device"],
                        **semantic_result,
                    }
                )
        output["ok"] = True
        return output, 0
    except Exception as exc:
        output["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        return output, 2


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['benchmark']}",
        "",
        f"- ok: `{payload['ok']}`",
        f"- top_k: `{payload['top_k']}`",
        f"- sqlite_db: `{payload['storage']['sqlite_db']}`",
        f"- lancedb_dir: `{payload['storage']['lancedb_dir']}`",
        "",
        "## Backend availability",
    ]
    for name, info in payload["backend_availability"].items():
        lines.append(f"- {name}: `{info}`")
    if payload.get("errors"):
        lines.extend(["", "## Errors"])
        for err in payload["errors"]:
            lines.append(f"- {err['type']}: {err['message']}")
    lines.extend(["", "## Results"])
    for result in payload.get("results", []):
        lines.append(
            f"### {result['query_id']} · {result['method']} · {result.get('latency_ms')} ms\n"
            f"> {result['query_text']}\n"
        )
        for cand in result.get("candidates", []):
            score = cand.get("score", cand.get("distance"))
            lines.append(f"{cand['rank']}. `{cand.get('id')}` score={score} — {cand.get('snippet', '')}")
        lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark existing SQLite FTS5 and LanceDB memory retrieval")
    parser.add_argument("--output", "-o", type=Path, help="Write output to this path instead of stdout")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="Output format")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload, exit_code = run_benchmark()
    payload = jsonable(payload)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) if args.format == "json" else render_markdown(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
