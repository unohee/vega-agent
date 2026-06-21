#!/usr/bin/env python3
# Created: 2026-06-21
# Purpose: Safely rebuild LanceDB memory vectors with the current memory_store.embed() implementation.
# Dependencies: pipeline/memory_store.py, lancedb, pyarrow

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import memory_store  # noqa: E402

LOG = logging.getLogger("rebuild_memory_vectors")
REQUIRED_COLUMNS = ("id", "person_id", "source", "text", "timestamp")
VECTOR_COLUMN = "vector"


class RebuildError(RuntimeError):
    pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Re-embed and replace only the LanceDB memory table using "
            "pipeline.memory_store.embed(). Defaults to dry-run."
        )
    )
    parser.add_argument("--commit", action="store_true", help="write the rebuilt memories table")
    parser.add_argument(
        "--delete-existing",
        action="store_true",
        help="replace memories without first creating an archive table",
    )
    parser.add_argument(
        "--archive-prefix",
        default="memories_archive",
        help="archive table prefix used before commit unless --delete-existing is set",
    )
    parser.add_argument(
        "--verify-query",
        default="memory",
        help="query passed to memory_store.search() after commit verification",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging verbosity",
    )
    return parser.parse_args(argv)


def table_count(table: Any) -> int:
    try:
        return int(table.count_rows())
    except TypeError:
        return int(table.count_rows(None))


def vector_dimension(value: Any) -> int | None:
    if value is None:
        return None
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        return len(value)
    except TypeError:
        return None


def load_source_rows(table: Any) -> tuple[list[dict[str, Any]], Any]:
    try:
        arrow_table = table.to_arrow()
    except AttributeError as exc:
        raise RebuildError("LanceDB table does not support to_arrow(); cannot safely snapshot rows") from exc
    rows = arrow_table.to_pylist()
    missing = [column for column in REQUIRED_COLUMNS if column not in arrow_table.column_names]
    if missing:
        raise RebuildError(f"memory table missing required columns: {missing}; columns={arrow_table.column_names}")
    return rows, arrow_table


def build_reembedded_rows(source_rows: Iterable[dict[str, Any]], expected_dim: int) -> list[dict[str, Any]]:
    rebuilt: list[dict[str, Any]] = []
    for index, row in enumerate(source_rows, start=1):
        text = str(row.get("text") or "")
        vector = memory_store.embed(text)
        actual_dim = vector_dimension(vector)
        if actual_dim != expected_dim:
            row_id = row.get("id")
            raise RebuildError(
                f"embedding dimension mismatch before commit at row {index} id={row_id!r}: "
                f"expected={expected_dim}, actual={actual_dim}"
            )
        rebuilt.append(
            {
                "id": str(row.get("id") or ""),
                "person_id": str(row.get("person_id") or "default"),
                "source": str(row.get("source") or "unknown"),
                "text": text,
                "timestamp": str(row.get("timestamp") or ""),
                VECTOR_COLUMN: [float(v) for v in vector],
            }
        )
    return rebuilt


def validate_rebuilt_rows(rows: Sequence[dict[str, Any]], expected_dim: int) -> None:
    bad_rows = [
        (row.get("id"), vector_dimension(row.get(VECTOR_COLUMN)))
        for row in rows
        if vector_dimension(row.get(VECTOR_COLUMN)) != expected_dim
    ]
    if bad_rows:
        sample = bad_rows[:5]
        raise RebuildError(f"rebuilt vector dimension validation failed: expected={expected_dim}, sample={sample}")


def make_rebuilt_arrow(rows: Sequence[dict[str, Any]]) -> Any:
    import pyarrow as pa

    return pa.table({key: [row[key] for row in rows] for key in (*REQUIRED_COLUMNS, VECTOR_COLUMN)}, schema=memory_store._schema())


def unique_archive_name(db: Any, prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{prefix}_{stamp}"
    table_names = set(db.table_names())
    if base not in table_names:
        return base
    suffix = 1
    while f"{base}_{suffix}" in table_names:
        suffix += 1
    return f"{base}_{suffix}"


def create_archive(db: Any, source_arrow: Any, prefix: str) -> str:
    archive_name = unique_archive_name(db, prefix)
    db.create_table(archive_name, data=source_arrow)
    return archive_name


def replace_memory_table(db: Any, rebuilt_rows: Sequence[dict[str, Any]]) -> None:
    rebuilt_arrow = make_rebuilt_arrow(rebuilt_rows)
    db.create_table(memory_store._TABLE_NAME, data=rebuilt_arrow, mode="overwrite")


def validate_committed_table(expected_count: int, expected_dim: int) -> None:
    table = memory_store._ensure_table()
    committed_count = table_count(table)
    if committed_count != expected_count:
        raise RebuildError(f"committed row count mismatch: expected={expected_count}, actual={committed_count}")
    rows, _ = load_source_rows(table)
    bad_rows = [
        (row.get("id"), vector_dimension(row.get(VECTOR_COLUMN)))
        for row in rows
        if vector_dimension(row.get(VECTOR_COLUMN)) != expected_dim
    ]
    if bad_rows:
        raise RebuildError(f"committed vector dimension mismatch: expected={expected_dim}, sample={bad_rows[:5]}")


def verify_search(query: str) -> int:
    results = memory_store.search(query, limit=1)
    return len(results)


def rebuild(args: argparse.Namespace) -> dict[str, Any]:
    expected_dim = int(memory_store._EMBED_DIM)
    db = memory_store._get_db()
    table_names = list(db.table_names())
    if memory_store._TABLE_NAME not in table_names:
        raise RebuildError(f"LanceDB table '{memory_store._TABLE_NAME}' not found; tables={table_names}")

    table = db.open_table(memory_store._TABLE_NAME)
    source_count = table_count(table)
    source_rows, source_arrow = load_source_rows(table)
    if source_count != len(source_rows):
        raise RebuildError(f"source count mismatch: count_rows={source_count}, to_arrow={len(source_rows)}")

    LOG.info("source_table=%s source_rows=%d expected_dim=%d", memory_store._TABLE_NAME, source_count, expected_dim)
    rebuilt_rows = build_reembedded_rows(source_rows, expected_dim)
    validate_rebuilt_rows(rebuilt_rows, expected_dim)
    LOG.info("reindexed_rows=%d dimension_validation=passed", len(rebuilt_rows))

    if not args.commit:
        LOG.info("dry_run=true commit=false archive_created=false table_replaced=false")
        return {
            "source_rows": source_count,
            "reindexed_rows": len(rebuilt_rows),
            "expected_dim": expected_dim,
            "dry_run": True,
        }

    archive_name = None
    if not args.delete_existing:
        archive_name = create_archive(db, source_arrow, args.archive_prefix)
        LOG.info("archive_table=%s archived_rows=%d", archive_name, source_count)
    else:
        LOG.warning("archive skipped by --delete-existing; only table '%s' will be overwritten", memory_store._TABLE_NAME)

    replace_memory_table(db, rebuilt_rows)
    validate_committed_table(source_count, expected_dim)
    search_count = verify_search(args.verify_query)
    LOG.info(
        "commit=true source_rows=%d reindexed_rows=%d expected_dim=%d search_results=%d archive_table=%s",
        source_count,
        len(rebuilt_rows),
        expected_dim,
        search_count,
        archive_name or "",
    )
    return {
        "source_rows": source_count,
        "reindexed_rows": len(rebuilt_rows),
        "expected_dim": expected_dim,
        "dry_run": False,
        "archive_table": archive_name,
        "search_results": search_count,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s %(message)s")
    try:
        rebuild(args)
    except RebuildError as exc:
        LOG.error("rebuild failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
