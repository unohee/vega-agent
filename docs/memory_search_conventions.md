# Memory search storage conventions

Discovery scope: `pipeline/data_paths.py`, `pipeline/memory_store.py`, existing memory/web scripts, and repo-wide searches for SQLite/FTS/LanceDB memory usage. This is a discovery note for the FTS5 vs BGE-M3 benchmark; production code is unchanged.

## Canonical SQLite DB path

Use `pipeline.data_paths.db_path()`.

Resolution in `pipeline/data_paths.py`:

1. `VEGA_DB_FILE` set to an absolute path: use it as-is.
2. `VEGA_DB_FILE` set to a relative name/path: resolve under `data_dir()`.
3. No override: `data_dir() / "agent.db"`.

`data_dir()` resolution:

1. `VEGA_DATA_DIR` if set.
2. macOS: `~/Library/Application Support/VEGA`.
3. Windows: `%LOCALAPPDATA%\VEGA` or `~/AppData/Local/VEGA`.
4. Linux/other: `~/.local/share/VEGA`.

Do not hard-code `agent.db`, `vega.db`, or platform-specific paths in benchmark code. Import the helper:

```python
from pipeline.data_paths import db_path
sqlite_path = db_path()
```

Known compatibility fallbacks exist in web code (`~/Library/Application Support/VEGA/agent.db` if importing `db_path` fails), but those are not canonical.

## SQLite memory schema currently present

The repo creates/uses these SQLite memory tables, not an FTS5 table:

- `persona_sections`
  - `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `section_key TEXT NOT NULL`
  - `content TEXT NOT NULL`
  - `scope TEXT DEFAULT 'global'` / `TEXT NOT NULL DEFAULT 'global'` depending creator
  - `source TEXT` in `pipeline.vega_query`
  - `version INTEGER DEFAULT 1`
  - `is_active INTEGER DEFAULT 1`
  - `notes TEXT`
  - `updated_at TEXT`
  - `ingested_at TEXT` in `pipeline.vega_query`
  - `user_edited INTEGER DEFAULT 0`
  - `sensitivity TEXT` added/ensured by `web.routers.memory_inspector`
- `events`
  - `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `event_date TEXT NOT NULL`
  - `date_raw TEXT` in `pipeline.vega_query`
  - `era TEXT` in `pipeline.vega_query`
  - `title TEXT NOT NULL`
  - `body TEXT NOT NULL DEFAULT ''`
  - `tags TEXT`
  - `ingested_at TEXT` in `pipeline.vega_query`
  - `created_at TEXT`
  - `sensitivity TEXT` added/ensured by `web.routers.memory_inspector`
- `entities`
  - `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `name TEXT NOT NULL`
  - `kind TEXT`
  - `canonical_id TEXT`
  - `aliases_json TEXT`
  - `notes TEXT`
  - `first_seen TEXT`
  - `last_seen TEXT`
  - `sensitivity TEXT` added/ensured by `web.routers.memory_inspector`
- `event_entities`
  - Created by `pipeline.vega_query` for event/entity joins.

`pipeline.vega_query._ensure_schema()` and `web.routers.memory_inspector._ensure_tables()` create the base tables if absent. For a benchmark, do not call these auto-creation helpers as a substitute for validating benchmark inputs; doing so can silently convert an absent corpus into empty tables.

## Existing FTS5 schema

No existing FTS5 memory table/schema was found.

Searches checked these patterns repo-wide or across memory/web scripts:

- `CREATE VIRTUAL TABLE.*fts`
- `USING fts5`
- `fts5(`
- `FTS5|fts5|VIRTUAL TABLE`
- `memory_fts|memories_fts|persona_memory|event_memory|entity_memory`

Result: no matches for a memory FTS5 virtual table and no `.sql` schema defining one.

Benchmark implication: any FTS5 benchmark path must either:

1. fail explicitly when the expected FTS5 table is absent, or
2. build a clearly named temporary/benchmark-only FTS5 index from canonical SQLite memory tables.

It must not imply that a production FTS5 memory table already exists.

## Canonical LanceDB path/table/vector conventions

Use `pipeline.memory_store` conventions.

- Directory helper: `pipeline.memory_store._lance_dir()`.
- Canonical path: `data_dir() / "lancedb"`.
- Fallback only if importing `pipeline.data_paths.data_dir` fails: `~/Library/Application Support/VEGA/lancedb`.
- Table name: `memories` (`_TABLE_NAME = "memories"`).
- Embedding dimension: `256` (`_EMBED_DIM = 256`).
- Vector column name: `vector`.
- LanceDB schema from `_schema()`:
  - `id: string`
  - `person_id: string`
  - `source: string`
  - `text: string`
  - `timestamp: string`
  - `vector: fixed-size list<float32>[256]`

Search uses:

```python
from pipeline.memory_store import embed
query_vector = embed(query)
table.search(query_vector).where("person_id = '...'").limit(limit).to_list()
```

Returned distance is read from LanceDB `_distance` and exposed as `score`.

## Required benchmark preflight checks

SQLite side:

1. Resolve path with `pipeline.data_paths.db_path()`.
2. Fail if the SQLite file does not exist. Do not create it for benchmark discovery.
3. Open read-only when possible (`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`) to avoid accidental schema creation.
4. Check required source tables exist before querying:
   - minimum corpus tables: `persona_sections`, `events`, `entities`.
   - if evaluating event/entity joins, also require `event_entities`.
5. Check required text columns exist:
   - `persona_sections`: `section_key`, `content`, `is_active`.
   - `events`: `event_date`, `title`, `body`.
   - `entities`: `name`, `kind`, `notes`.
6. If using an existing FTS5 table, verify it exists with `sqlite_master` and that its indexed columns match the benchmark query plan. Current repo evidence says this table is absent.
7. Fail if all selected source tables are empty after filters; otherwise a search benchmark can report misleading zero-hit timings.

LanceDB side:

1. Use `pipeline.memory_store._lance_dir()` or reproduce its canonical `data_dir() / "lancedb"` resolution.
2. Fail if the directory is absent when benchmarking existing vector data. Do not call `_ensure_table()` for validation because it creates an empty `memories` table.
3. Connect with `lancedb.connect(str(path))`.
4. Check table `memories` exists in `db.table_names()`.
5. Check required columns exist in the table schema:
   - `id`, `person_id`, `source`, `text`, `timestamp`, `vector`.
6. Check `vector` is a 256-dimensional float32 vector/list field, matching `_EMBED_DIM` and `_schema()`.
7. Fail if the table has zero rows after any benchmark filters.

## Explicit failure cases

The benchmark should stop with a clear error in these cases:

- `pipeline.data_paths.db_path()` resolves to a missing SQLite DB file.
- SQLite DB exists but cannot be opened read-only.
- Required SQLite source tables are missing.
- Required SQLite columns are missing or incompatible.
- An FTS5 mode is requested but no FTS5 table exists and the benchmark was not explicitly configured to build a temporary index.
- A discovered FTS5 table has unexpected columns/content mapping.
- SQLite source corpus is empty after filters.
- LanceDB directory is missing for an existing-vector benchmark.
- LanceDB cannot be opened.
- LanceDB table `memories` is missing.
- LanceDB table is missing required scalar columns or `vector`.
- LanceDB `vector` dimension/type is not the repo convention: float32, 256 dimensions.
- LanceDB corpus is empty after filters.
- Embedding generation falls back unexpectedly if the benchmark requires a specific embedding model. `pipeline.memory_store.embed()` can fall back to deterministic hash vectors when EXAONE load fails, so benchmark code must report the embedding backend actually used rather than silently mixing model/hash results.
