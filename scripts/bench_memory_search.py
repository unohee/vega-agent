#!/usr/bin/env python3
# Created: 2026-06-21
# Purpose: Reproducible SQLite FTS5-prefix/BM25 vs BGE-M3/LanceDB memory-search gate.
# Dependencies: pipeline/data_paths.py, sqlite3 FTS5, lancedb, sentence-transformers or fastembed

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import platform
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.data_paths import data_dir, db_path  # noqa: E402

TOP_K = 5
PASS_THRESHOLD_PP = 10.0
MODEL_NAME = "BAAI/bge-m3"
LANCEDB_TABLE = "memories"
BGE_M3_DIM = 256

# Fixed representative user-memory queries. Keep stable for comparable gate runs.
QUERIES = [
    "current project priorities and blockers",
    "user communication preferences email slack meeting style",
    "important deadlines and upcoming dates",
    "people I work with and their roles",
    "personal preferences for travel food schedule",
    "recent decisions and rationale",
    "open tasks I promised to complete",
    "technical architecture choices for current work",
    "recurring problems or incidents to remember",
    "customer client stakeholder feedback",
    "documents files or repositories mentioned recently",
    "health family or personal constraints",
    "budget pricing contract subscription details",
    "login account device setup notes",
    "follow up reminders from conversations",
]

STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "or",
    "the",
    "their",
    "to",
    "with",
}


class BenchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FtsTable:
    name: str
    text_columns: tuple[str, ...]
    metadata_columns: tuple[str, ...]


class BgeM3Embedder:
    def __init__(self, backend: str, device: str | None = None) -> None:
        self.backend = backend
        self.device = device
        self.model: Any = None
        self.load_seconds = 0.0
        self._load()

    def _load(self) -> None:
        start = time.perf_counter()
        if self.backend == "sentence-transformers":
            try:
                from sentence_transformers import SentenceTransformer
            except Exception as exc:  # pragma: no cover - environment dependent
                raise BenchError(f"sentence-transformers backend unavailable: {exc}") from exc
            kwargs = {"device": self.device} if self.device else {}
            self.model = SentenceTransformer(MODEL_NAME, **kwargs)
            self.device = str(getattr(self.model, "device", self.device or "unknown"))
            self.load_seconds = time.perf_counter() - start
            return
        if self.backend == "fastembed":
            try:
                from fastembed import TextEmbedding
            except Exception as exc:  # pragma: no cover - environment dependent
                raise BenchError(f"fastembed backend unavailable: {exc}") from exc
            try:
                self.model = TextEmbedding(model_name=MODEL_NAME)
            except Exception as exc:  # pragma: no cover - environment dependent
                raise BenchError(f"fastembed cannot load required {MODEL_NAME} model: {exc}") from exc
            self.device = "fastembed-default"
            self.load_seconds = time.perf_counter() - start
            return
        raise BenchError(f"unsupported embedding backend: {self.backend}")

    def encode_query(self, text: str) -> list[float]:
        if self.backend == "sentence-transformers":
            vec = self.model.encode(text, normalize_embeddings=True, show_progress_bar=False)
            return _as_float_list(vec)
        if self.backend == "fastembed":
            vec = next(iter(self.model.query_embed([text])))
            return _as_float_list(vec)
        raise BenchError(f"unsupported embedding backend: {self.backend}")


def _as_float_list(vec: Any) -> list[float]:
    if hasattr(vec, "tolist"):
        vec = vec.tolist()
    return [float(v) for v in vec]


def detect_backends() -> dict[str, Any]:
    st_available = importlib.util.find_spec("sentence_transformers") is not None
    torch_available = importlib.util.find_spec("torch") is not None
    mps_available = False
    if torch_available:
        try:
            import torch

            mps_available = bool(torch.backends.mps.is_available())
        except Exception as exc:  # environment probe only; report the reason instead of hiding it
            return {
                "required_model": MODEL_NAME,
                "sentence_transformers": {
                    "available": st_available,
                    "torch_available": torch_available,
                    "mps_available": False,
                    "mps_probe_error": str(exc),
                },
                "fastembed": {"available": importlib.util.find_spec("fastembed") is not None},
                "mlx": _detect_mlx(),
            }
    return {
        "required_model": MODEL_NAME,
        "sentence_transformers": {
            "available": st_available,
            "torch_available": torch_available,
            "mps_available": mps_available,
        },
        "fastembed": {"available": importlib.util.find_spec("fastembed") is not None},
        "mlx": _detect_mlx(),
    }


def _detect_mlx() -> dict[str, Any]:
    return {
        "mlx_available": importlib.util.find_spec("mlx") is not None,
        "mlx_lm_available": importlib.util.find_spec("mlx_lm") is not None,
        "candidate_only": True,
        "note": "No MLX BGE-M3 embedder path is wired in this repo; availability recorded only.",
    }


def choose_backend(requested: str, availability: dict[str, Any]) -> tuple[str, str | None]:
    if requested == "sentence-transformers":
        if not availability["sentence_transformers"]["available"]:
            raise BenchError("required real embedding backend missing: sentence-transformers")
        device = "mps" if availability["sentence_transformers"].get("mps_available") else None
        return requested, device
    if requested == "fastembed":
        if not availability["fastembed"]["available"]:
            raise BenchError("required real embedding backend missing: fastembed")
        return requested, None
    if requested != "auto":
        raise BenchError(f"unknown backend {requested!r}")
    if availability["sentence_transformers"]["available"]:
        device = "mps" if availability["sentence_transformers"].get("mps_available") else None
        return "sentence-transformers", device
    if availability["fastembed"]["available"]:
        return "fastembed", None
    raise BenchError("required real embedding backend missing: install sentence-transformers or fastembed")


def ensure_sqlite_db() -> Path:
    path = db_path()
    if not path.exists():
        raise BenchError(f"SQLite DB missing: {path}")
    if not path.is_file():
        raise BenchError(f"SQLite DB path is not a file: {path}")
    return path


def connect_readonly(path: Path) -> sqlite3.Connection:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise BenchError(f"cannot open SQLite DB read-only: {path}: {exc}") from exc
    con.row_factory = sqlite3.Row
    return con


def discover_fts_table(con: sqlite3.Connection, requested: str | None) -> FtsTable:
    rows = con.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'table' AND sql IS NOT NULL AND lower(sql) LIKE '%using fts5%'"
    ).fetchall()
    candidates = {str(r["name"]): str(r["sql"] or "") for r in rows}
    if requested:
        if requested not in candidates:
            raise BenchError(f"FTS5 schema missing: table {requested!r} not found")
        names = [requested]
    else:
        names = sorted(candidates, key=lambda n: ("memor" not in n.lower(), n.lower()))
    for name in names:
        cols = [str(r[1]) for r in con.execute(f"PRAGMA table_info({_quote_ident(name)})")]
        text_cols = tuple(c for c in cols if c.lower() in {"text", "content", "body", "title", "notes", "name"})
        if not text_cols:
            text_cols = tuple(c for c in cols if not c.startswith("_"))
        if text_cols:
            metadata_cols = tuple(c for c in cols if c not in text_cols)
            return FtsTable(name=name, text_columns=text_cols, metadata_columns=metadata_cols)
    raise BenchError("FTS5 schema missing: no usable FTS5 virtual table found")


def validate_fts_nonempty(con: sqlite3.Connection, table: FtsTable) -> int:
    count = int(con.execute(f"SELECT count(*) FROM {_quote_ident(table.name)}").fetchone()[0])
    if count <= 0:
        raise BenchError(f"FTS5 table is empty: {table.name}")
    return count


def run_fts_query(con: sqlite3.Connection, table: FtsTable, query: str) -> tuple[float, list[dict[str, Any]]]:
    match = make_prefix_match(query)
    select_cols = ["rowid"] + list(table.text_columns) + list(table.metadata_columns)
    select_sql = ", ".join(f"{_quote_ident(c)}" for c in select_cols if c != "rowid")
    sql = (
        f"SELECT rowid, {select_sql}, bm25({_quote_ident(table.name)}) AS score "
        f"FROM {_quote_ident(table.name)} WHERE {_quote_ident(table.name)} MATCH ? "
        "ORDER BY score LIMIT ?"
    )
    start = time.perf_counter()
    rows = con.execute(sql, (match, TOP_K)).fetchall()
    elapsed = time.perf_counter() - start
    results = []
    for rank, row in enumerate(rows, start=1):
        item = row_to_result(row, rank, score_key="score")
        text = " ".join(str(row[c] or "") for c in table.text_columns)
        item["snippet"] = make_snippet(text, query)
        item["relevance"] = score_relevance(query, text)
        results.append(item)
    return elapsed, results


def make_prefix_match(query: str) -> str:
    terms = query_terms(query)
    if not terms:
        raise BenchError(f"query has no searchable terms: {query!r}")
    return " AND ".join(f'"{term}"*' for term in terms)


def ensure_lancedb_table() -> Any:
    lance_dir = data_dir() / "lancedb"
    if not lance_dir.exists():
        raise BenchError(f"LanceDB data missing: {lance_dir}")
    try:
        import lancedb
    except Exception as exc:
        raise BenchError(f"lancedb package unavailable: {exc}") from exc
    try:
        db = lancedb.connect(str(lance_dir))
        names = db.table_names()
    except Exception as exc:
        raise BenchError(f"cannot open LanceDB at {lance_dir}: {exc}") from exc
    if LANCEDB_TABLE not in names:
        raise BenchError(f"LanceDB table missing: {lance_dir}/{LANCEDB_TABLE}")
    try:
        table = db.open_table(LANCEDB_TABLE)
    except Exception as exc:
        raise BenchError(f"cannot open LanceDB table {LANCEDB_TABLE}: {exc}") from exc
    validate_lancedb_schema(table)
    return table


def validate_lancedb_schema(table: Any) -> int:
    try:
        schema = table.schema
        names = set(schema.names)
    except Exception as exc:
        raise BenchError(f"cannot read LanceDB schema: {exc}") from exc
    required = {"id", "text", "vector"}
    missing = required - names
    if missing:
        raise BenchError(f"LanceDB schema missing columns: {sorted(missing)}")
    vector_field = schema.field("vector")
    if str(BGE_M3_DIM) not in str(vector_field.type):
        raise BenchError(f"LanceDB vector column is not BGE-M3 {BGE_M3_DIM}-dim: {vector_field.type}")
    try:
        count = int(table.count_rows())
    except Exception as exc:
        raise BenchError(f"cannot count LanceDB rows: {exc}") from exc
    if count <= 0:
        raise BenchError(f"LanceDB table is empty: {LANCEDB_TABLE}")
    return count


def run_semantic_query(table: Any, embedder: BgeM3Embedder, query: str) -> tuple[float, list[dict[str, Any]]]:
    encode_start = time.perf_counter()
    vector = embedder.encode_query(query)
    encode_seconds = time.perf_counter() - encode_start
    if len(vector) != BGE_M3_DIM:
        raise BenchError(
            f"embedding dimension mismatch for {MODEL_NAME}: got {len(vector)}, expected {BGE_M3_DIM}; "
            "LanceDB memories must be built with real BGE-M3 vectors"
        )
    start = time.perf_counter()
    try:
        rows = table.search(vector).limit(TOP_K).to_list()
    except Exception as exc:
        raise BenchError(f"LanceDB semantic search failed: {exc}") from exc
    elapsed = time.perf_counter() - start + encode_seconds
    results = []
    for rank, row in enumerate(rows, start=1):
        item = dict_to_result(row, rank)
        item["distance"] = _coerce_float(row.get("_distance"))
        item["snippet"] = make_snippet(item["text"], query)
        item["relevance"] = score_relevance(query, item["text"])
        results.append(item)
    return elapsed, results


def score_relevance(query: str, text: str | None) -> int:
    """Deterministic 0-2 rubric: 0=no lexical signal, 1=partial, 2=strong topical match."""
    if not text:
        return 0
    terms = query_terms(query)
    if not terms:
        return 0
    normalized = " ".join(re.findall(r"[\w]+", text.lower()))
    hits = sum(1 for term in terms if term in normalized)
    coverage = hits / len(terms)
    if coverage >= 0.50 or hits >= 4:
        return 2
    if coverage >= 0.25 or hits >= 2:
        return 1
    return 0


def top5_relevance_pct(results: list[dict[str, Any]]) -> float:
    return 100.0 * sum(int(r.get("relevance") or 0) for r in results[:TOP_K]) / (TOP_K * 2)


def query_terms(query: str) -> list[str]:
    return [t for t in re.findall(r"[\w]+", query.lower()) if len(t) > 1 and t not in STOPWORDS]


def row_to_result(row: sqlite3.Row, rank: int, score_key: str) -> dict[str, Any]:
    data = {k: row[k] for k in row.keys()}
    text = first_present(data, ("text", "content", "body", "title", "notes", "name")) or ""
    return {
        "rank": rank,
        "id": str(data.get("id") or data.get("rowid")),
        "text": text,
        "score": _coerce_float(data.get(score_key)),
        "source": data.get("source"),
        "timestamp": first_present(data, ("timestamp", "updated_at", "ingested_at", "created_at", "event_date", "last_seen")),
        "person": first_present(data, ("person", "person_id", "user", "scope")),
    }


def dict_to_result(row: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "id": str(row.get("id") or row.get("_rowid") or ""),
        "text": str(row.get("text") or ""),
        "score": _coerce_float(row.get("score")),
        "source": row.get("source"),
        "timestamp": row.get("timestamp") or row.get("updated_at") or row.get("created_at"),
        "person": row.get("person_id") or row.get("person") or row.get("user"),
    }


def first_present(data: dict[str, Any], names: Iterable[str]) -> str | None:
    for name in names:
        value = data.get(name)
        if value is not None and str(value) != "":
            return str(value)
    return None


def make_snippet(text: str, query: str, width: int = 240) -> str:
    if len(text) <= width:
        return text
    terms = query_terms(query)
    lower = text.lower()
    pos = min((lower.find(t) for t in terms if lower.find(t) >= 0), default=0)
    start = max(0, pos - width // 3)
    return text[start : start + width]


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return ordered[idx]


def summarize(flat_rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    latencies = [float(r["latency_ms"]) for r in flat_rows if r["method"] == method]
    pcts = [float(r[f"{method}_relevance_pct"]) for r in flat_rows if r["method"] == method and "rank" not in r]
    return {
        "queries": len(pcts),
        "top5_relevance_pct_avg": sum(pcts) / len(pcts) if pcts else None,
        "latency_ms_min": min(latencies) if latencies else None,
        "latency_ms_p50": percentile(latencies, 0.50),
        "latency_ms_p95": percentile(latencies, 0.95),
        "latency_ms_max": max(latencies) if latencies else None,
    }


def default_output_dir() -> Path:
    return ROOT / "benchmark_reports" / "memory_search"


def resolve_output_dir(arg: str | None) -> Path:
    return Path(arg).expanduser() if arg else default_output_dir()


def write_outputs(out_dir: Path, payload: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "memory_search_benchmark.json"
    csv_path = out_dir / "memory_search_benchmark.csv"
    md_path = out_dir / "memory_search_benchmark.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(csv_path, rows)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, csv_path, md_path


def write_csv(csv_path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "query_index",
        "query",
        "method",
        "latency_ms",
        "rank",
        "id",
        "text",
        "snippet",
        "score",
        "distance",
        "source",
        "timestamp",
        "person",
        "relevance",
        "fts5_relevance_pct",
        "bge_m3_relevance_pct",
        "delta_pp",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    verdict = payload["verdict"]
    lines = [
        "# Memory search benchmark: FTS5-prefix/BM25 vs BGE-M3",
        "",
        f"- Created: {payload['created_at']}",
        f"- SQLite DB: `{payload['sqlite_db']}` ({payload['sqlite_rows']} FTS5 rows)",
        f"- LanceDB: `{payload['lancedb_path']}/{payload['lancedb_table']}` ({payload['lancedb_rows']} rows)",
        f"- Top-k: {payload['top_k']}",
        f"- Relevance rubric: {payload['relevance_rubric']}",
        f"- Embedding backend: {payload['embedding_backend']}",
        f"- Backend availability: `{json.dumps(payload['backend_availability'], sort_keys=True)}`",
        "",
        "## Gate verdict",
        "",
        f"**{verdict['status']}** — BGE-M3 delta {verdict['delta_pp']:.1f} pp; threshold >= {verdict['threshold_pp']:.1f} pp.",
        "",
        "| method | avg top-5 relevance % | p50 latency ms | p95 latency ms | max latency ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for method in ("fts5", "bge_m3"):
        item = summary[method]
        lines.append(
            f"| {method} | {_fmt(item['top5_relevance_pct_avg'])} | {_fmt(item['latency_ms_p50'])} | "
            f"{_fmt(item['latency_ms_p95'])} | {_fmt(item['latency_ms_max'])} |"
        )
    lines.extend(["", "## Per-query results", ""])
    lines.append("| # | query | FTS5 % | BGE-M3 % | delta pp | FTS5 ids(rel) | BGE-M3 ids(rel) |")
    lines.append("|---:|---|---:|---:|---:|---|---|")
    for result in payload["results"]:
        fts = result["fts5"]
        bge = result["bge_m3"]
        lines.append(
            f"| {result['query_index']} | {result['query']} | {_fmt(fts['relevance_pct'])} | {_fmt(bge['relevance_pct'])} | "
            f"{_fmt(result['delta_pp'])} | {result_ids(fts['results'])} | {result_ids(bge['results'])} |"
        )
    lines.extend(
        [
            "",
            "## Backend notes",
            "",
            "- sentence-transformers(MPS): selected when `torch.backends.mps.is_available()` is true; load time is included in backend metadata, per-query semantic latency includes query encoding + LanceDB search.",
            "- fastembed: selected with `--backend fastembed` or auto fallback; this run records availability even when not selected.",
            "- MLX: candidate availability is recorded only; no repo-local MLX BGE-M3 embedding path is wired into this gate.",
            "- If verdict is FAIL, later BGE-M3 semantic-axis implementation subtasks must stop.",
            "",
        ]
    )
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f}"


def result_ids(results: list[dict[str, Any]]) -> str:
    return ", ".join(f"{r.get('id', '')}({r.get('relevance', 0)})" for r in results) or "-"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark memory search: SQLite FTS5 BM25 vs LanceDB BGE-M3.")
    parser.add_argument("--output", "-o", help="Output directory. Defaults to benchmark_reports/memory_search")
    parser.add_argument("--backend", choices=("auto", "sentence-transformers", "fastembed"), default="auto")
    parser.add_argument("--fts-table", help="Explicit FTS5 table name. Defaults to first memory-like FTS5 table.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    sqlite_path = ensure_sqlite_db()
    availability = detect_backends()
    backend, device = choose_backend(args.backend, availability)
    table = ensure_lancedb_table()
    lancedb_rows = validate_lancedb_schema(table)
    embedder = BgeM3Embedder(backend=backend, device=device)
    flat_rows: list[dict[str, Any]] = []
    query_results: list[dict[str, Any]] = []

    with connect_readonly(sqlite_path) as con:
        fts_table = discover_fts_table(con, args.fts_table)
        sqlite_rows = validate_fts_nonempty(con, fts_table)
        for idx, query in enumerate(QUERIES, start=1):
            fts_latency, fts_results = run_fts_query(con, fts_table, query)
            semantic_latency, semantic_results = run_semantic_query(table, embedder, query)
            fts_pct = top5_relevance_pct(fts_results)
            bge_pct = top5_relevance_pct(semantic_results)
            delta_pp = bge_pct - fts_pct
            query_results.append(
                {
                    "query_index": idx,
                    "query": query,
                    "fts5": {"latency_ms": fts_latency * 1000.0, "relevance_pct": fts_pct, "results": fts_results},
                    "bge_m3": {
                        "latency_ms": semantic_latency * 1000.0,
                        "relevance_pct": bge_pct,
                        "results": semantic_results,
                    },
                    "delta_pp": delta_pp,
                }
            )
            append_flat_rows(flat_rows, idx, query, "fts5", fts_latency, fts_results, fts_pct, bge_pct, delta_pp)
            append_flat_rows(flat_rows, idx, query, "bge_m3", semantic_latency, semantic_results, fts_pct, bge_pct, delta_pp)
            flat_rows.append(
                {
                    "query_index": idx,
                    "query": query,
                    "method": "fts5",
                    "latency_ms": fts_latency * 1000.0,
                    "fts5_relevance_pct": fts_pct,
                    "bge_m3_relevance_pct": bge_pct,
                    "delta_pp": delta_pp,
                }
            )
            flat_rows.append(
                {
                    "query_index": idx,
                    "query": query,
                    "method": "bge_m3",
                    "latency_ms": semantic_latency * 1000.0,
                    "fts5_relevance_pct": fts_pct,
                    "bge_m3_relevance_pct": bge_pct,
                    "delta_pp": delta_pp,
                }
            )

    fts_avg = sum(r["fts5"]["relevance_pct"] for r in query_results) / len(query_results)
    bge_avg = sum(r["bge_m3"]["relevance_pct"] for r in query_results) / len(query_results)
    aggregate_delta = bge_avg - fts_avg
    status = "PASS" if aggregate_delta >= PASS_THRESHOLD_PP else "FAIL"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_at_unix": time.time(),
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "sqlite_db": str(sqlite_path),
        "sqlite_rows": sqlite_rows,
        "data_dir": str(data_dir()),
        "lancedb_path": str(data_dir() / "lancedb"),
        "lancedb_table": LANCEDB_TABLE,
        "lancedb_rows": lancedb_rows,
        "top_k": TOP_K,
        "pass_threshold_pp": PASS_THRESHOLD_PP,
        "queries": QUERIES,
        "relevance_rubric": "per result: 0=no lexical signal, 1=partial query-term coverage, 2=strong query-term coverage; top-5 % = sum(scores)/(5*2)*100",
        "fts5": {"table": fts_table.name, "text_columns": list(fts_table.text_columns)},
        "embedding_backend": {
            "backend": backend,
            "device": embedder.device,
            "model": MODEL_NAME,
            "model_load_ms": embedder.load_seconds * 1000.0,
        },
        "backend_availability": availability,
        "summary": {"fts5": summarize(flat_rows, "fts5"), "bge_m3": summarize(flat_rows, "bge_m3")},
        "verdict": {"status": status, "delta_pp": aggregate_delta, "threshold_pp": PASS_THRESHOLD_PP},
        "results": query_results,
    }
    json_path, csv_path, md_path = write_outputs(resolve_output_dir(args.output), payload, flat_rows)
    print(f"verdict: {status} delta_pp={aggregate_delta:.1f} threshold_pp={PASS_THRESHOLD_PP:.1f}")
    print(f"wrote JSON: {json_path}")
    print(f"wrote CSV: {csv_path}")
    print(f"wrote report: {md_path}")
    return 0 if status == "PASS" else 1


def append_flat_rows(
    flat_rows: list[dict[str, Any]],
    query_index: int,
    query: str,
    method: str,
    latency_seconds: float,
    results: list[dict[str, Any]],
    fts_pct: float,
    bge_pct: float,
    delta_pp: float,
) -> None:
    if not results:
        flat_rows.append(
            {
                "query_index": query_index,
                "query": query,
                "method": method,
                "latency_ms": latency_seconds * 1000.0,
                "fts5_relevance_pct": fts_pct,
                "bge_m3_relevance_pct": bge_pct,
                "delta_pp": delta_pp,
            }
        )
        return
    for result in results:
        row = {
            "query_index": query_index,
            "query": query,
            "method": method,
            "latency_ms": latency_seconds * 1000.0,
            "fts5_relevance_pct": fts_pct,
            "bge_m3_relevance_pct": bge_pct,
            "delta_pp": delta_pp,
            **result,
        }
        flat_rows.append(row)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
