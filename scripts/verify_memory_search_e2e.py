#!/usr/bin/env python3
# Created: 2026-06-21
# Purpose: End-to-end memory search verification: FTS5-only vs production hybrid top-5.
# Dependencies: pipeline.hybrid_search, pipeline.memory_store, pipeline.vega_query

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.data_paths import data_dir, db_path  # noqa: E402

TOP_K = 5
PASS_THRESHOLD_PP = 10.0
STRONG_SEMANTIC_DISTANCE = 0.80


@dataclass(frozen=True)
class QueryCase:
    query: str
    kind: str
    expected: str
    note: str


QUERY_CASES = [
    QueryCase(
        "KYTE 음악 라이선스 AI 인프라 담당자",
        "semantic",
        "오희원은 KYTE에서 음악 라이선스 AI 인프라를 담당한다",
        "BGE-M3-only memory row; exact Korean semantic memory should surface.",
    ),
    QueryCase(
        "KYTE 음악 라이선스 AI 인프라",
        "semantic",
        "오희원은 KYTE에서 음악 라이선스 AI 인프라를 담당한다",
        "Same semantic target without the role noun.",
    ),
    QueryCase(
        "오희원 KYTE 음악 라이선스 AI 인프라",
        "semantic",
        "오희원은 KYTE에서 음악 라이선스 AI 인프라를 담당한다",
        "Proper name plus semantic role context.",
    ),
    QueryCase(
        "음악 라이선스 AI 인프라 담당",
        "semantic",
        "오희원은 KYTE에서 음악 라이선스 AI 인프라를 담당한다",
        "Semantic paraphrase with organization omitted.",
    ),
    QueryCase("KYTE AX", "proper_noun", "KYTE AX", "Exact entity/project name."),
    QueryCase("VEGA Tauri App", "proper_noun", "VEGA Tauri App", "Exact entity/project name."),
    QueryCase("de-artifact", "proper_noun", "de-artifact", "Exact product/project name."),
    QueryCase("STONKS", "proper_noun", "STONKS", "Exact project/topic name."),
    QueryCase("ArtifactNet", "proper_noun", "ArtifactNet", "Exact project/topic name."),
    QueryCase("LanceDB", "proper_noun", "LanceDB", "Exact technical entity name."),
]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run memory search E2E verification.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "benchmark_reports" / "memory_search_e2e",
        help="Directory for JSON/Markdown verification outputs.",
    )
    parser.add_argument(
        "--keep-temp-db",
        action="store_true",
        help="Keep the writable SQLite backup used for vega_query/server import verification.",
    )
    return parser.parse_args(list(argv))


def backup_sqlite_to_temp(source: Path) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temp_dir = tempfile.TemporaryDirectory(prefix="vega_memory_verify_", dir="/private/tmp")
    target = Path(temp_dir.name) / "vega.db"
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return temp_dir, target


def text_of(row: dict[str, Any]) -> str:
    return str(row.get("text") or row.get("snippet") or "")


def result_brief(row: dict[str, Any]) -> dict[str, Any]:
    text = text_of(row).replace("\n", " ")
    return {
        "source": row.get("source"),
        "id": row.get("id"),
        "lexical_rank": row.get("lexical_rank"),
        "vector_rank": row.get("vector_rank"),
        "vector_score": row.get("vector_score"),
        "vector_confidence_bonus": row.get("vector_confidence_bonus"),
        "fused_score": row.get("fused_score"),
        "text": text[:220],
    }


def contains_expected(rows: list[dict[str, Any]], expected: str) -> bool:
    needle = expected.lower()
    return any(needle in text_of(row).lower() for row in rows[:TOP_K])


def fts5_only_search(hybrid_mod: Any, query: str) -> list[dict[str, Any]]:
    return hybrid_mod.hybrid_search(
        query,
        limit=TOP_K,
        vector_searcher=lambda *_args, **_kwargs: [],
    )


def summarize_cases(cases: list[dict[str, Any]], method: str) -> dict[str, Any]:
    total = len(cases)
    passed = sum(1 for case in cases if case[method]["passed"])
    by_kind: dict[str, dict[str, int | float]] = {}
    for case in cases:
        kind = case["kind"]
        item = by_kind.setdefault(kind, {"passed": 0, "total": 0, "pct": 0.0})
        item["total"] = int(item["total"]) + 1
        item["passed"] = int(item["passed"]) + int(bool(case[method]["passed"]))
    for item in by_kind.values():
        item["pct"] = 100.0 * int(item["passed"]) / int(item["total"])
    return {"passed": passed, "total": total, "pct": 100.0 * passed / total, "by_kind": by_kind}


def vector_dimension_from_schema(table: Any) -> int | None:
    try:
        field_type = str(table.schema.field("vector").type)
    except Exception:
        return None
    marker = "["
    if marker not in field_type or not field_type.endswith("]"):
        return None
    try:
        return int(field_type.rsplit("[", 1)[1].rstrip("]"))
    except ValueError:
        return None


def vector_len(value: Any) -> int | None:
    if value is None:
        return None
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        return len(value)
    except TypeError:
        return None


def dot(left: list[float], right: list[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right)))


def norm(vec: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vec))


def json_response_body(resp: Any) -> dict[str, Any]:
    return json.loads(resp.body.decode("utf-8"))


async def inspector_smoke(memory_inspector: Any) -> dict[str, Any]:
    summary = json_response_body(await memory_inspector.memory_summary())
    entities = json_response_body(await memory_inspector.list_entities(kind="", search="KYTE", limit=5, offset=0))
    events = json_response_body(await memory_inspector.list_events(search="ChatGPT", tag="", limit=5, offset=0))
    persona = json_response_body(await memory_inspector.list_persona(active_only=True, search="KYTE"))
    return {
        "summary_keys": sorted(summary.keys()),
        "entities_kyte_total": entities.get("total"),
        "entities_kyte_rows": len(entities.get("rows", [])),
        "events_chatgpt_total": events.get("total"),
        "events_chatgpt_rows": len(events.get("rows", [])),
        "persona_kyte_rows": len(persona.get("rows", [])),
    }


def scoped_sha256_hits() -> list[dict[str, Any]]:
    files = [
        ROOT / "pipeline" / "memory_store.py",
        ROOT / "pipeline" / "hybrid_search.py",
        ROOT / "scripts" / "rebuild_memory_vectors.py",
    ]
    hits: list[dict[str, Any]] = []
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "sha256" in line.lower():
                hits.append({"file": str(path.relative_to(ROOT)), "line": lineno, "text": line.strip()})
    return hits


def render_markdown(payload: dict[str, Any]) -> str:
    verdict = payload["verdict"]
    lines = [
        "# Memory search E2E verification",
        "",
        f"- Created: {payload['created_at']}",
        f"- SQLite source: `{payload['paths']['sqlite_source']}`",
        f"- SQLite verification copy: `{payload['paths']['sqlite_copy']}`",
        f"- LanceDB: `{payload['paths']['lancedb_dir']}`",
        f"- Top-k: {TOP_K}",
        f"- Gate: hybrid top-5 pass rate must beat FTS5-only by >= {PASS_THRESHOLD_PP:.1f} pp.",
        "",
        "## Verdict",
        "",
        f"**{verdict['status']}** - delta {verdict['delta_pp']:.1f} pp "
        f"(FTS5 {verdict['fts5_pct']:.1f}%, hybrid {verdict['hybrid_pct']:.1f}%).",
        "",
        "## Regression checks",
        "",
        f"- Import check: `{payload['import_check']['status']}`; "
        f"memory_store embedder after server import: `{payload['import_check']['embedder_after_server_import']}`",
        f"- LanceDB rows/dimension: {payload['lancedb']['row_count']} rows, vector_dim={payload['lancedb']['vector_dimension']}",
        f"- Embedding cosine sanity: `{payload['embedding_cosine_sanity']['status']}`; "
        f"target={payload['embedding_cosine_sanity']['target_cosine']:.4f}, "
        f"unrelated={payload['embedding_cosine_sanity']['unrelated_cosine']:.4f}",
        f"- SHA256 embedding fallback hits in scoped files: {len(payload['sha256_fallback_check']['hits'])}",
        f"- Inspector smoke: `{payload['inspector_smoke']['status']}`",
        "",
        "## Query results",
        "",
        "| kind | query | expected | FTS5 pass | hybrid pass | hybrid top hit |",
        "|---|---|---|---:|---:|---|",
    ]
    for case in payload["cases"]:
        top = case["hybrid"]["results"][0] if case["hybrid"]["results"] else {}
        lines.append(
            f"| {case['kind']} | {case['query']} | {case['expected']} | "
            f"{'yes' if case['fts5']['passed'] else 'no'} | "
            f"{'yes' if case['hybrid']['passed'] else 'no'} | "
            f"{top.get('source', '')}:{top.get('id', '')} |"
        )
    lines.extend(
        [
            "",
            "## Commands",
            "",
            "```bash",
            payload["command"],
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(output_dir: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "memory_search_e2e_verification.json"
    md_path = output_dir / "memory_search_e2e_verification.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    started = time.perf_counter()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    sqlite_source = db_path()
    temp_db, sqlite_copy = backup_sqlite_to_temp(sqlite_source)
    os.environ["VEGA_DB_FILE"] = str(sqlite_copy)

    command = " ".join(["python", sys.argv[0], *sys.argv[1:]]) if sys.argv else "python scripts/verify_memory_search_e2e.py"
    payload: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "paths": {
            "sqlite_source": str(sqlite_source),
            "sqlite_copy": str(sqlite_copy),
            "data_dir": str(data_dir()),
            "lancedb_dir": str(data_dir() / "lancedb"),
        },
        "environment": {
            "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"),
            "TRANSFORMERS_OFFLINE": os.environ.get("TRANSFORMERS_OFFLINE"),
            "VEGA_DB_FILE": os.environ.get("VEGA_DB_FILE"),
        },
        "cases": [],
        "errors": [],
    }

    exit_code = 0
    try:
        memory_store = importlib.import_module("pipeline.memory_store")
        payload["import_check"] = {
            "memory_store_import_seconds": None,
            "embedder_after_memory_store_import": repr(memory_store._embedder),
        }
        server_start = time.perf_counter()
        server = importlib.import_module("web.server")
        payload["import_check"].update(
            {
                "status": "PASS",
                "server_import_seconds": (time.perf_counter() - server_start),
                "embedder_after_server_import": repr(memory_store._embedder),
                "server_routes": len(getattr(server, "app").routes),
            }
        )
        if memory_store._embedder is not None:
            raise RuntimeError("memory_store embedder was loaded during server import")

        hybrid_mod = importlib.import_module("pipeline.hybrid_search")
        memory_inspector = importlib.import_module("web.routers.memory_inspector")

        db = memory_store._get_db()
        table = db.open_table(memory_store._TABLE_NAME)
        sample = table.to_arrow().slice(0, 1).to_pylist()
        sample_vector_dim = vector_len(sample[0].get("vector")) if sample else None
        payload["lancedb"] = {
            "table": memory_store._TABLE_NAME,
            "row_count": int(table.count_rows()),
            "schema": str(table.schema),
            "vector_dimension": vector_dimension_from_schema(table),
            "sample_vector_dimension": sample_vector_dim,
        }
        if payload["lancedb"]["vector_dimension"] != memory_store._EMBED_DIM:
            raise RuntimeError(
                f"LanceDB vector dimension mismatch: {payload['lancedb']['vector_dimension']} != {memory_store._EMBED_DIM}"
            )
        if sample_vector_dim != memory_store._EMBED_DIM:
            raise RuntimeError(f"sample vector dimension mismatch: {sample_vector_dim} != {memory_store._EMBED_DIM}")

        for case in QUERY_CASES:
            fts_rows = fts5_only_search(hybrid_mod, case.query)
            hybrid_rows = hybrid_mod.hybrid_search(case.query, limit=TOP_K)
            payload["cases"].append(
                {
                    "query": case.query,
                    "kind": case.kind,
                    "expected": case.expected,
                    "note": case.note,
                    "fts5": {
                        "passed": contains_expected(fts_rows, case.expected),
                        "results": [result_brief(row) for row in fts_rows],
                    },
                    "hybrid": {
                        "passed": contains_expected(hybrid_rows, case.expected),
                        "results": [result_brief(row) for row in hybrid_rows],
                    },
                }
            )

        fts5_summary = summarize_cases(payload["cases"], "fts5")
        hybrid_summary = summarize_cases(payload["cases"], "hybrid")
        delta_pp = hybrid_summary["pct"] - fts5_summary["pct"]
        semantic_ok = hybrid_summary["by_kind"].get("semantic", {}).get("pct") == 100.0
        proper_ok = hybrid_summary["by_kind"].get("proper_noun", {}).get("pct") == 100.0
        gate_ok = delta_pp >= PASS_THRESHOLD_PP and semantic_ok and proper_ok

        target_vec = memory_store.embed(QUERY_CASES[0].expected)
        close_vec = memory_store.embed("KYTE 음악 라이선스 AI 인프라")
        unrelated_vec = memory_store.embed("무관한 양자역학 축구 레시피")
        target_cosine = dot(target_vec, close_vec)
        unrelated_cosine = dot(target_vec, unrelated_vec)
        payload["embedding_cosine_sanity"] = {
            "status": "PASS" if target_cosine > unrelated_cosine and norm(target_vec) > 0.99 else "FAIL",
            "target_cosine": target_cosine,
            "unrelated_cosine": unrelated_cosine,
            "target_norm": norm(target_vec),
            "close_norm": norm(close_vec),
            "unrelated_norm": norm(unrelated_vec),
            "strong_semantic_distance_threshold": STRONG_SEMANTIC_DISTANCE,
        }

        payload["sha256_fallback_check"] = {"hits": scoped_sha256_hits()}
        payload["inspector_smoke"] = {"status": "PASS", **asyncio.run(inspector_smoke(memory_inspector))}
        payload["summary"] = {"fts5": fts5_summary, "hybrid": hybrid_summary}
        payload["verdict"] = {
            "status": "PASS" if gate_ok else "FAIL",
            "delta_pp": delta_pp,
            "threshold_pp": PASS_THRESHOLD_PP,
            "fts5_pct": fts5_summary["pct"],
            "hybrid_pct": hybrid_summary["pct"],
            "semantic_ok": semantic_ok,
            "proper_noun_ok": proper_ok,
        }
        if not gate_ok or payload["embedding_cosine_sanity"]["status"] != "PASS" or payload["sha256_fallback_check"]["hits"]:
            exit_code = 1
    except Exception as exc:
        payload.setdefault("import_check", {"status": "NOT_RUN"})
        payload.setdefault("verdict", {"status": "ERROR", "delta_pp": 0.0, "threshold_pp": PASS_THRESHOLD_PP})
        payload["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        exit_code = 1
    finally:
        payload["total_latency_ms"] = (time.perf_counter() - started) * 1000.0
        json_path, md_path = write_outputs(args.output_dir, payload)
        print(f"verdict: {payload.get('verdict', {}).get('status')}")
        if "summary" in payload:
            print(
                "top5_pass_rate: "
                f"fts5={payload['summary']['fts5']['pct']:.1f}% "
                f"hybrid={payload['summary']['hybrid']['pct']:.1f}% "
                f"delta={payload['verdict']['delta_pp']:.1f}pp"
            )
        print(f"wrote JSON: {json_path}")
        print(f"wrote report: {md_path}")
        if args.keep_temp_db:
            print(f"kept temp DB: {sqlite_copy}")
        else:
            temp_db.cleanup()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
