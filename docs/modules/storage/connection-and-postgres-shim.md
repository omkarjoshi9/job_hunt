# Connection layer and the Postgres shim

- **Source**: `src/jobdigest/db/connection.py` (226 lines), `src/jobdigest/db/_common.py` (46 lines)
- **Layer**: persistence, lowest level. Everything else in [the storage package](overview.md) goes through these objects.
- **Depends on**: `sqlite3`, optional `psycopg` (imported lazily), `jobdigest.paths.repo_root`, `jobdigest.normalize.normalize_url` (only `norm_http_url`).
- **Used by**: all other `db/*` modules; `connect` is called by `collect.py`, `score.py`, `digest.py`, `bot.py`, `sheet.py`, `contacts/run.py`.
- **Tests**: `tests/test_db_internals.py` (`test_pg_sql_skips_question_marks_in_literals`, `test_pg_sql_returning_only_for_id_tables`, `test_safe_ident`, `test_transaction_commits_and_rolls_back`), `tests/test_db_postgres.py` (`test_connect_path_ignores_database_url`, `test_uses_postgres_when_url_set_and_no_path`, `test_pg_schema_keeps_identity_rules`, `test_pg_sql_placeholders_and_returning`), `tests/test_db_upsert.py::test_foreign_keys_on`.

## Why this exists

The code base was written against SQLite (`sqlite3.Row`, `?` placeholders, `cursor.lastrowid`). To run the same code on PostgreSQL (for hosted CI where a file DB would not persist) without an ORM, `connection.py` provides a thin wrapper, `_PgConnection`, that exposes the small subset of the `sqlite3.Connection` API the code uses (`execute`, `commit`, `rollback`, `close`, `executescript`) and rewrites SQL on the fly. All business SQL in the package is therefore written once, in SQLite dialect, and must stay portable to Postgres (for example `ON CONFLICT ... DO UPDATE` is used in `upsert_score`; `INSERT OR IGNORE` is not used anywhere in `db/`).

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `connect` | `connect(path: Path \| None = None) -> Conn` | Open the DB (SQLite or Postgres), create/migrate schema |
| `connect_postgres` | `connect_postgres(url: str) -> _PgConnection` | Open a psycopg connection and init schema |
| `uses_postgres` | `uses_postgres(path: Path \| None = None) -> bool` | True when no explicit path and `DATABASE_URL` is set |
| `postgres_url` | `postgres_url() -> str` | Stripped `DATABASE_URL` env var or `""` |
| `default_db_path` | `default_db_path() -> Path` | `DATABASE_PATH` env or `<repo>/data/jobs.db` |
| `transaction` | `transaction(conn)` context manager | Commit on success, rollback on any exception |
| `safe_ident` | `safe_ident(name: str) -> str` | Whitelist check before interpolating identifiers into SQL |
| `table_columns` | `table_columns(conn, table) -> set[str]` | Column names of a table on either engine |
| `MappingRow` | class | dict-backed row supporting `row["x"]` and `row[0]` |
| `_PgConnection` / `_PgCursor` | classes | sqlite-shaped wrappers over psycopg |
| `_pg_sql` | `_pg_sql(sql: str) -> str` | `?` to `%s` and optional `RETURNING id` |
| `is_pg`, `split_sql`, `ID_TABLES` | helpers/constant | not re-exported from the package root |

## Key concepts

**SQLite WAL.** SQLite normally uses a rollback journal where writers block readers. In *write-ahead-log* mode (`PRAGMA journal_mode = WAL`) writes are appended to a separate `-wal` file and readers keep reading the last committed snapshot, so the Telegram bot process can read while a collector run writes. The setting is persistent in the DB file once set. That is why `data/jobs.db-wal` and `-shm` files may appear next to the database.

**`lastrowid`.** SQLite exposes the auto-generated primary key of the last INSERT as `cursor.lastrowid`. Postgres has no equivalent, so the shim appends `RETURNING id` to inserts into tables known to have an `id` column and puts the returned value into a synthesized `lastrowid`.

## Detailed walkthrough

### `safe_ident`
**Location** `src/jobdigest/db/connection.py:25` · **Signature** `def safe_ident(name: str) -> str`
Validates `name` against `_IDENT_RE = ^[A-Za-z_][A-Za-z0-9_]{0,62}$` (letter or underscore first, then up to 62 letters/digits/underscores; 63 total, the Postgres identifier limit). Returns `name` unchanged or raises `ValueError("unsafe SQL identifier: ...")`. A non-`str` also raises. Needed because table/column names cannot be bound as `?` parameters, so the few places that interpolate them (`table_columns`, `_add_missing_columns`, constraint dropping in the Postgres migration) validate first. **Called by** `table_columns`, `schema._add_missing_columns`, `schema._migrate_pg_applications_status`. Covered by `test_safe_ident`.

### `MappingRow`
**Location** `src/jobdigest/db/connection.py:32` · A minimal row object for Postgres results. psycopg is opened with `row_factory=dict_row`, so each row is a `dict`; `MappingRow` wraps it so that code written for `sqlite3.Row` works. Fields: `_data` (copy of the dict), `_keys`, `_values`. `__getitem__` takes a `str` (dict lookup, `KeyError` if absent) or an `int` (positional index into `_values`, `IndexError` if out of range). `__contains__` tests key membership; `keys()` returns the dict keys. It has no `get`, no `__iter__` and no `__len__`; code in the package uses `row_get` (from `_common`) rather than `.get`. Positional access mirrors `sqlite3.Row` and is used e.g. by `row[0]` in `schema._sqlite_applications_sql`.

### `_adapt_row`
**Location** `src/jobdigest/db/connection.py:50` · `def _adapt_row(row: Any) -> RowLike | None`. Returns `None` for `None`, passes a `MappingRow` through, wraps a `dict` in `MappingRow`, and returns anything else untouched (this is the SQLite path: `sqlite3.Row` objects are returned as-is). Called by `_PgCursor.fetchone/fetchall` and `_PgConnection.execute`.

### `_replace_placeholders`
**Location** `src/jobdigest/db/connection.py:60` · `def _replace_placeholders(sql: str) -> str`
Rewrites each `?` to `%s` (psycopg's paramstyle) except inside quotes.

1. `quote` is `None` while outside a literal.
2. For each character: if inside a quote, copy it and, when it equals the opening quote char, leave the quote. A doubled quote (`''`) closes and immediately reopens on the next character, so escaped quotes keep working.
3. Outside a quote: `'` or `"` starts a quote; `?` becomes `%s`; anything else is copied.

Verified: `_pg_sql("INSERT INTO jobs (a) VALUES (?, '?')")` returns `INSERT INTO jobs (a) VALUES (%s, '?') RETURNING id`. Limitations: it does not understand SQL comments or dollar-quoting, and a literal `%` in SQL would be misread by psycopg as a placeholder (none of the SQL in `db/` contains one; `LIKE` patterns would need care). Test: `test_pg_sql_skips_question_marks_in_literals`.

### `_wants_returning_id` and `ID_TABLES`
**Location** `src/jobdigest/db/connection.py:79` (`ID_TABLES` at line 16, `_INSERT_TARGET_RE` at line 19)
`ID_TABLES = {companies, jobs, scores, runs, portal_errors, applications, contacts}`: the tables with an `id` primary key. `_INSERT_TARGET_RE` matches `INSERT [OR <word>] INTO [""]name` at the start of a statement, case-insensitive. `_wants_returning_id(sql)` is True when: the statement does not already contain `RETURNING` (case-insensitive), the regex matches, and the lowercased table is in `ID_TABLES`. So `INSERT INTO foo ...` and `UPDATE ...` never get `RETURNING id` (Verified; test `test_pg_sql_returning_only_for_id_tables`). Note the `INSERT OR IGNORE INTO scores` case also matches (the regex accepts `OR <word>`), producing invalid Postgres SQL; nothing in `db/` issues such a statement.

### `_pg_sql`
**Location** `src/jobdigest/db/connection.py:86` · `def _pg_sql(sql: str) -> str`
1. `rewritten = _replace_placeholders(sql)`.
2. If `_wants_returning_id(rewritten)`: `rewritten.rstrip().rstrip(";") + " RETURNING id"`, i.e. a trailing semicolon is removed first. Else return as is.

### `_PgCursor`
**Location** `src/jobdigest/db/connection.py:93` · Wraps a psycopg cursor. `__init__(cur, lastrowid=None)` stores `_cur` and sets `lastrowid` to the given value or `0`. `fetchone()`/`fetchall()` delegate and adapt rows via `_adapt_row`. It provides only these; callers never iterate a cursor directly in this package.

### `split_sql`
**Location** `src/jobdigest/db/connection.py:105` · `def split_sql(schema: str) -> list[str]`: `schema.split(";")`, strip each part, drop empties. Naive: a `;` inside a string literal would split wrongly. The schema text contains none. Used by `_PgConnection.executescript`, `schema.init_schema`, `schema._migrate_contacts_table`.

### `_PgConnection`
**Location** `src/jobdigest/db/connection.py:109` · Responsibility: look like a `sqlite3.Connection` over psycopg.

| Method | Behaviour |
|---|---|
| `execute(sql, parameters=())` (line 113) | see below |
| `commit()` / `rollback()` / `close()` | delegate to the psycopg connection |
| `executescript(script)` (line 137) | runs each `split_sql` statement through the *raw* `self._conn.execute` (so no placeholder rewriting and no cursor returned) |

`execute` step by step:
1. `pg_sql = _pg_sql(sql)`; `cur = self._conn.execute(pg_sql, parameters)` (psycopg 3 connections have `execute`, returning a cursor).
2. `_wants_returning_id(sql)` is evaluated on the *original* SQL. If true, the statement ended with `RETURNING id`, so `cur.fetchone()` yields `{"id": n}`; `lastrowid` is set to `int(adapted["id"])`, falling back to `0` on `KeyError/TypeError/ValueError` or if there is no row.
3. Returns `_PgCursor(cur, lastrowid=...)` for those inserts, or `_PgCursor(cur)` otherwise.

Design pattern: adapter/facade. Consequences: `conn.row_factory`, `conn.total_changes`, `cursor.rowcount` and `with conn:` are **not** provided; code in the package does not use them. Postgres transactions are implicit (psycopg opens one on first statement) and ended by `commit()`/`rollback()`; SQLite's `sqlite3` module likewise opens a transaction before DML.

### `_PgConnection.execute`

**Location** `src/jobdigest/db/connection.py:113` · **Signature** `def execute(self, sql: str, parameters: Any = ()) -> _PgCursor`
**Purpose** Lets code written for `sqlite3.Connection.execute` (`?` placeholders, `cursor.lastrowid`, rows readable by name or index) run unchanged on PostgreSQL. SQLite hands back the new row id for free; Postgres does not, so inserts into `id` tables get `RETURNING id` appended and the id is read back into `lastrowid`.
**Called by** every `conn.execute(...)` in `jobdigest.db.*` when `connect` returned a Postgres connection (including `table_columns`, `schema.init_schema`, `transaction` users). **Calls** `_pg_sql`, the psycopg connection's `execute`, `_wants_returning_id`, `_adapt_row`, `_PgCursor`.
**Parameters** `sql` (SQLite-dialect text with `?`), `parameters` (tuple or sequence passed straight to psycopg, never rewritten). **Returns** a `_PgCursor`: `lastrowid` is the inserted id for id-table inserts, else `0`. Exceptions from psycopg propagate (nothing is caught except around the id conversion).

**Step by step**
1. `pg_sql = _pg_sql(sql)`: `?` -> `%s` outside quoted literals, and ` RETURNING id` appended (after stripping a trailing `;`) when `_wants_returning_id` says so.
2. `cur = self._conn.execute(pg_sql, parameters)`: the single round trip.
3. `_wants_returning_id(sql)` is evaluated again on the *original* text (equivalent result, since the original has no `RETURNING`). If false: `return _PgCursor(cur)` (`lastrowid == 0`; `fetchone`/`fetchall` still work for a `SELECT`).
4. If true: `lastrowid = 0`; `row = cur.fetchone()` consumes the single returned row; if not `None`, `adapted = _adapt_row(row)` (a psycopg `dict_row` dict becomes a `MappingRow`) and `lastrowid = int(adapted["id"])`; `KeyError`, `TypeError`, `ValueError` leave it at `0`.
5. `return _PgCursor(cur, lastrowid=lastrowid)`. Because step 4 already fetched the row, a later `cur.fetchone()` on that cursor yields nothing.
6. Row wrapping happens lazily: `_PgCursor.fetchone/fetchall` pass each psycopg dict through `_adapt_row`, giving `MappingRow` objects that support `row["name"]`, `row[0]`, `"name" in row` and `.keys()` like `sqlite3.Row`.

```python
pg_sql = _pg_sql(sql)
cur = self._conn.execute(pg_sql, parameters)
if _wants_returning_id(sql):
    lastrowid = 0
    row = cur.fetchone()
```
**Verified offline** with a fake raw connection recording what it receives (rows given as dicts like psycopg's `dict_row`):

| Input SQL | SQL sent to the driver | `lastrowid` |
|---|---|---|
| `INSERT INTO jobs (title, note) VALUES (?, '?');` (fake returned `{"id": 42}`) | `INSERT INTO jobs (title, note) VALUES (%s, '?') RETURNING id` | `42` |
| `INSERT INTO jobs (title) VALUES (?)` (fake returned no row) | `... VALUES (%s) RETURNING id` | `0` |
| same, row `{"x": 1}` (no `id` key) | `... RETURNING id` | `0` |
| `INSERT INTO foo (a) VALUES (?)` (table not in `ID_TABLES`) | `INSERT INTO foo (a) VALUES (%s)` | `0` |
| `INSERT INTO runs (a) VALUES (?) RETURNING id` | unchanged except `%s` | `0` (the caller owns the `RETURNING` row) |
| `SELECT * FROM jobs WHERE id = ?` (row `{"id":1,"title":"T"}`) | `SELECT * FROM jobs WHERE id = %s` | `0`; `fetchone()` gives a `MappingRow` with `row["id"] == 1`, `row[1] == "T"`, `"title" in row`, `list(row.keys()) == ["id","title"]` |

The note about the quoted `'?'` literal being left alone is the point of `_replace_placeholders`; the trailing `;` is removed only when `RETURNING id` is appended (otherwise the original is sent with its `;`).
**Plain-English summary** Translate the query to Postgres syntax, run it, and for inserts into tables with an `id` column fetch the generated id so `cursor.lastrowid` works like it does in SQLite. Tested by `tests/test_db_internals.py::test_pg_sql_returning_only_for_id_tables` and `tests/test_db_postgres.py::test_pg_sql_placeholders_and_returning` (these exercise `_pg_sql`; I did not find a test that runs `_PgConnection.execute` against a fake driver).

### `is_pg`
`connection.py:142`: `isinstance(conn, _PgConnection)`. Used by `table_columns`, `schema.init_schema`, `schema._migrate_applications_status`, `schema._migrate_contacts_table`.

### `transaction`
**Location** `src/jobdigest/db/connection.py:147` · **Signature** `@contextmanager def transaction(conn: Conn) -> Iterator[Conn]`
Purpose: one unit of work equals one commit; guarantees rollback on error.
1. `yield conn` inside `try`.
2. `except BaseException`: attempt `conn.rollback()` (errors from rollback itself are swallowed, `# pragma: no cover`), then re-raise. `BaseException` means `KeyboardInterrupt` also rolls back.
3. `else`: `conn.commit()`.

Subtlety: `return` inside the `with` body (used by `upsert_job`, `upsert_application`, `upsert_company`, ...) is not an exception, so the `else` branch still commits. There is no nesting support: an inner `transaction` would commit the outer unit. Callers never nest them (`upsert_job` calls `link_duplicate_jobs` etc. which execute without opening their own `transaction`). Test: `test_transaction_commits_and_rolls_back`; `test_upsert_job_single_commit` checks `upsert_job` commits once.

### `postgres_url`, `uses_postgres`, `default_db_path`
- `postgres_url()` (`:161`): `(os.environ.get("DATABASE_URL") or "").strip()`.
- `uses_postgres(path=None)` (`:165`): `path is None and bool(postgres_url())`. An explicit `path` always forces SQLite, even when `DATABASE_URL` is set; this is why tests that pass `tmp_path / "jobs.db"` are never redirected to Postgres (`test_connect_path_ignores_database_url`).
- `default_db_path()` (`:169`): if `DATABASE_PATH` is set, expand `~`, make relative paths relative to `repo_root()`, `.resolve()`; otherwise `<repo_root>/data/jobs.db`.

### `table_columns`
**Location** `src/jobdigest/db/connection.py:179` · `def table_columns(conn: Conn, table: str) -> set[str]`
1. `table = safe_ident(table)`.
2. Postgres: query `information_schema.columns` filtered to `table_schema = 'public'` and the table name (bound as parameter) and collect `row["name"]`.
3. SQLite: `PRAGMA table_info(<table>)` (identifier interpolated, hence `safe_ident`); name from `row["name"]`, falling back to `row[1]` if the row type is not name-addressable.
Returns an empty set when the table does not exist (used by `_migrate_contacts_table` as "does the table exist"). Re-exported also as `_table_columns`.

### `connect_postgres`
**Location** `src/jobdigest/db/connection.py:201` · imports `psycopg` and `dict_row` lazily (so SQLite-only environments never need psycopg installed), imports `init_schema` lazily (the schema module imports this one, so a top-level import would be circular), opens `psycopg.connect(url, row_factory=dict_row)`, wraps it, calls `init_schema(conn)`, returns it.

### `connect`
**Location** `src/jobdigest/db/connection.py:213` · **Signature** `def connect(path: Path | None = None) -> Conn`
1. If `uses_postgres(path)`: `return connect_postgres(postgres_url())`.
2. Else `db_path = Path(path)` or `default_db_path()`; create parent directories.
3. `sqlite3.connect(db_path)`; `conn.row_factory = sqlite3.Row` (name-addressable rows).
4. `PRAGMA foreign_keys = ON` (SQLite leaves FK enforcement off by default, per connection) and `PRAGMA journal_mode = WAL`.
5. `init_schema(conn)` (create tables, migrate, commit; see [schema-and-migrations.md](schema-and-migrations.md)).
6. `PRAGMA foreign_keys = ON` again, because the applications rebuild migration (`_rebuild_sqlite_applications`) turns it OFF/ON internally and this guarantees the final state.

Returns an open connection; the caller must `close()` it. The connection is created with default `check_same_thread=True`, so it must not be shared across threads.

## `_common.py` helpers

| Symbol | Line | Behaviour |
|---|---|---|
| `Conn = Any`, `RowLike = Any` | 6-7 | type aliases only |
| `utc_now()` | 10 | `datetime.now(timezone.utc)` without microseconds, ISO string with `+00:00` replaced by `Z`, e.g. `2026-11-03T14:00:02Z` |
| `now()` | 19 | `import jobdigest.db as pkg; return pkg.utc_now()` so monkeypatching `jobdigest.db.utc_now` works |
| `row_get(row, key, default=None)` | 30 | `row[key]`, returning `default` on `KeyError/IndexError/TypeError` (works for both row types, which lack `.get`) |
| `norm_http_url(raw)` | 37 | strip; return `None` unless it starts with `http://`/`https://` (case-insensitive); else `normalize_url(text)`, again requiring an http(s) result. Used by `applications.py` so only real URLs get an `apply_url_norm` there (contrast `normalize.job_identity`, which allows synthetic `ats://` keys for jobs) |

## Failure modes and edge cases

- `DATABASE_URL` set but `psycopg` not installed: `connect_postgres` raises `ImportError`.
- `INSERT` with an explicit `RETURNING` is passed through unchanged and `lastrowid` is `0`.
- `INSERT ... ON CONFLICT DO UPDATE` into an `ID_TABLES` table (`upsert_score`) gets `RETURNING id` appended; fine on Postgres because `DO UPDATE` returns a row. `DO NOTHING` would return no row and yield `lastrowid == 0`.
- `executescript` bypasses `?` rewriting; do not put parameters in scripts.
- Rows from Postgres are `MappingRow`, which has no `.get()`; use `row_get` or `row["col"]`.

## How to modify / extend safely

- Adding a table with an `id` primary key: add it to `ID_TABLES`, or `lastrowid` will be `0` on Postgres.
- Keep new SQL free of SQLite-only syntax and of `%` characters; use `?` placeholders only.
- Interpolating a table/column name? Wrap it in `safe_ident`.
- Use `with transaction(conn):` for every write; do not nest.
