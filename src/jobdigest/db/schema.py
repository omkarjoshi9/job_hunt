from __future__ import annotations

from jobdigest.db.connection import (
    is_pg,
    safe_ident,
    split_sql,
    table_columns,
)
from jobdigest.db._common import Conn, row_get

APP_STATUS_CHECK = (
    "status IN ('applied','oa','interview','offer','rejected','ghosted','saved','hidden')"
)

# Columns added after the first release.  They are declared once here and used
# both to build the CREATE TABLE statements and to migrate older databases.
ADDED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "companies": (("dead_streak", "INTEGER NOT NULL DEFAULT 0"),),
    "jobs": (
        ("miss_count", "INTEGER NOT NULL DEFAULT 0"),
        ("closed_at", "TEXT"),
        ("close_reason", "TEXT"),
        ("employment_hint", "TEXT"),
        ("workplace_hint", "TEXT"),
        ("notified_at", "TEXT"),
        ("notify_kind", "TEXT"),
        ("description_hash", "TEXT"),
        ("raw_title", "TEXT"),
        ("raw_company", "TEXT"),
        ("posting_key", "TEXT"),
        ("canonical_job_id", "INTEGER REFERENCES jobs(id)"),
        ("legitimacy_flag", "TEXT"),
        ("legitimacy_reasons_json", "TEXT"),
        ("posted_at", "TEXT"),
    ),
    "scores": (("stale", "INTEGER NOT NULL DEFAULT 0"),),
}

# Indexes that reference migrated columns; created after migrate_schema has
# added those columns (so they also work on databases created by old releases).
LATE_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "jobs_posting_key",
        "CREATE INDEX IF NOT EXISTS jobs_posting_key "
        "ON jobs(posting_key) WHERE posting_key IS NOT NULL AND posting_key != ''",
    ),
    (
        "jobs_description_hash",
        "CREATE INDEX IF NOT EXISTS jobs_description_hash "
        "ON jobs(description_hash) WHERE description_hash IS NOT NULL",
    ),
    (
        "jobs_source_active",
        "CREATE INDEX IF NOT EXISTS jobs_source_active ON jobs(source, is_active)",
    ),
    (
        "jobs_canonical_job_id",
        "CREATE INDEX IF NOT EXISTS jobs_canonical_job_id "
        "ON jobs(canonical_job_id) WHERE canonical_job_id IS NOT NULL",
    ),
)


def _create_table(
    name: str,
    base: tuple[str, ...],
    constraints: tuple[str, ...] = (),
    *,
    prefix: str = "CREATE TABLE IF NOT EXISTS",
) -> str:
    added = tuple(f"{col} {ddl}" for col, ddl in ADDED_COLUMNS.get(name, ()))
    cols = base + added + constraints
    body = ",\n  ".join(cols)
    return f"{prefix} {name} (\n  {body}\n)"


_COMPANIES = (
    "id INTEGER PRIMARY KEY",
    "slug TEXT NOT NULL UNIQUE",
    "display_name TEXT NOT NULL",
    "ats TEXT NOT NULL",
    "board_token TEXT NOT NULL",
    "enabled INTEGER NOT NULL DEFAULT 0",
    "resolved_url TEXT",
    "last_fetch_at TEXT",
    "last_http_status INTEGER",
    "last_job_count INTEGER",
    "last_error TEXT",
)

_JOBS = (
    "id INTEGER PRIMARY KEY",
    "apply_url TEXT",
    "apply_url_norm TEXT NOT NULL UNIQUE",
    "source TEXT NOT NULL",
    "external_id TEXT",
    "company_id INTEGER REFERENCES companies(id)",
    "company TEXT",
    "title TEXT NOT NULL",
    "location_raw TEXT",
    "description_text TEXT",
    "raw_json TEXT NOT NULL",
    "first_seen_at TEXT NOT NULL",
    "last_seen_at TEXT NOT NULL",
    "is_active INTEGER NOT NULL DEFAULT 1",
    "hard_status TEXT NOT NULL DEFAULT 'pending'\n"
    "    CHECK (hard_status IN ('pending','pass','reject'))",
    "hard_reason TEXT",
    "ats TEXT NOT NULL DEFAULT ''",
)

_SCORES = (
    "id INTEGER PRIMARY KEY",
    "job_id INTEGER NOT NULL REFERENCES jobs(id)",
    "model TEXT NOT NULL",
    "prompt_version TEXT NOT NULL",
    "relevant INTEGER NOT NULL",
    "score REAL",
    "reasons_json TEXT",
    "created_at TEXT NOT NULL",
)

_RUNS = (
    "id INTEGER PRIMARY KEY",
    "kind TEXT NOT NULL",
    "started_at TEXT NOT NULL",
    "finished_at TEXT",
    "status TEXT NOT NULL CHECK (status IN ('running','ok','error'))",
    "stats_json TEXT",
    "error TEXT",
)

_PORTAL_ERRORS = (
    "id INTEGER PRIMARY KEY",
    "run_id INTEGER REFERENCES runs(id)",
    "portal_name TEXT NOT NULL",
    "url TEXT NOT NULL",
    "error_class TEXT NOT NULL",
    "http_status INTEGER",
    "detail TEXT",
    "occurred_at TEXT NOT NULL",
)

_APPLICATIONS = (
    "id INTEGER PRIMARY KEY",
    "job_id INTEGER REFERENCES jobs(id)",
    "apply_url_norm TEXT NOT NULL UNIQUE",
    "company TEXT",
    "title TEXT",
    "applied_on TEXT NOT NULL",
    f"status TEXT NOT NULL CHECK ({APP_STATUS_CHECK})",
    "source TEXT",
    "notes TEXT",
    "sheet_row INTEGER",
    "synced_at TEXT",
)

CONTACT_SOURCES = frozenset({"jd", "github", "pattern"})
_CONTACTS = (
    "id INTEGER PRIMARY KEY",
    "job_id INTEGER REFERENCES jobs(id)",
    "company TEXT NOT NULL",
    "domain TEXT",
    "name TEXT NOT NULL",
    "email TEXT",
    "role TEXT",
    "source TEXT NOT NULL\n    CHECK (source IN ('jd','github','pattern'))",
    "verified TEXT NOT NULL DEFAULT 'unverified'",
    "confidence REAL",
    "created_at TEXT NOT NULL",
)
_CONTACTS_UNIQUE = ("UNIQUE(job_id, name, source)",)

_SCHEMA_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS jobs_source_ext
  ON jobs(source, external_id) WHERE external_id IS NOT NULL AND external_id != '';
CREATE INDEX IF NOT EXISTS jobs_hard_active
  ON jobs(hard_status, is_active);
"""

_CONTACT_INDEXES = """
CREATE INDEX IF NOT EXISTS contacts_job_id
  ON contacts(job_id) WHERE job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS contacts_company
  ON contacts(company);
"""

SCHEMA = (
    ";\n\n".join(
        [
            _create_table("companies", _COMPANIES),
            _create_table("jobs", _JOBS)
            + ";\n"
            + _SCHEMA_INDEXES.strip().rstrip(";"),
            _create_table("scores", _SCORES, ("UNIQUE(job_id, model, prompt_version)",)),
            _create_table("runs", _RUNS),
            _create_table("portal_errors", _PORTAL_ERRORS),
            _create_table("applications", _APPLICATIONS),
            _create_table("contacts", _CONTACTS, _CONTACTS_UNIQUE)
            + ";\n"
            + _CONTACT_INDEXES.strip().rstrip(";"),
        ]
    )
    + ";\n"
    + ";\n".join(sql for _name, sql in LATE_INDEXES)
    + ";\n"
)

_PK_PLAIN = "id INTEGER PRIMARY KEY"
_PK_PG = "id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
SCHEMA_PG = SCHEMA.replace(_PK_PLAIN, _PK_PG)

# Used when the contacts table must be created by migration on an old database.
_CONTACTS_CREATE_SQLITE = _create_table(
    "contacts", _CONTACTS, _CONTACTS_UNIQUE, prefix="CREATE TABLE"
)
_CONTACTS_CREATE_PG = _CONTACTS_CREATE_SQLITE.replace(_PK_PLAIN, _PK_PG)
_APP_CREATE_SQLITE = _create_table("applications", _APPLICATIONS, prefix="CREATE TABLE")


def _add_missing_columns(conn: Conn, table: str) -> None:
    table = safe_ident(table)
    cols = table_columns(conn, table)
    if not cols:
        return
    for col, ddl in ADDED_COLUMNS.get(table, ()):
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {safe_ident(col)} {ddl}")


def _sql_allows_saved(sql: str | None) -> bool:
    return "'saved'" in (sql or "")


def _sqlite_applications_sql(conn: Conn) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'"
    ).fetchone()
    if row is None:
        return None
    return str(row_get(row, "sql", row[0]) or "")


def _rebuild_sqlite_applications(conn: Conn) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE applications RENAME TO applications_migrate_old")
    conn.execute(_APP_CREATE_SQLITE)
    conn.execute(
        """
        INSERT INTO applications (
          id, job_id, apply_url_norm, company, title, applied_on, status,
          source, notes, sheet_row, synced_at
        )
        SELECT id, job_id, apply_url_norm, company, title, applied_on, status,
               source, notes, sheet_row, synced_at
        FROM applications_migrate_old
        """
    )
    conn.execute("DROP TABLE applications_migrate_old")
    conn.execute("PRAGMA foreign_keys = ON")


def _migrate_pg_applications_status(conn: Conn) -> None:
    rows = conn.execute(
        """
        SELECT con.conname AS name, pg_get_constraintdef(con.oid) AS def
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
        WHERE nsp.nspname = 'public'
          AND rel.relname = 'applications'
          AND con.contype = 'c'
        """
    ).fetchall()
    defs = []
    for row in rows:
        try:
            defs.append((str(row["name"]), str(row["def"] or "")))
        except (KeyError, IndexError, TypeError):
            defs.append((str(row[0]), str(row[1] or "")))
    if any(_sql_allows_saved(defn) for _name, defn in defs):
        return
    status_names = [
        name
        for name, defn in defs
        if "status" in defn.lower() or "ghosted" in defn.lower()
    ]
    for name in status_names:
        try:
            ident = safe_ident(name)
        except ValueError:
            continue
        conn.execute(f'ALTER TABLE applications DROP CONSTRAINT "{ident}"')
    conn.execute(
        "ALTER TABLE applications ADD CONSTRAINT applications_status_check "
        f"CHECK ({APP_STATUS_CHECK})"
    )


def _migrate_applications_status(conn: Conn) -> None:
    if is_pg(conn):
        _migrate_pg_applications_status(conn)
        return
    sql = _sqlite_applications_sql(conn)
    if sql is None or _sql_allows_saved(sql):
        return
    _rebuild_sqlite_applications(conn)


def _migrate_contacts_table(conn: Conn) -> None:
    if table_columns(conn, "contacts"):
        return
    conn.execute(_CONTACTS_CREATE_PG if is_pg(conn) else _CONTACTS_CREATE_SQLITE)
    for stmt in split_sql(_CONTACT_INDEXES):
        conn.execute(stmt)


def _create_late_indexes(conn: Conn) -> None:
    for _name, sql in LATE_INDEXES:
        conn.execute(sql)


def migrate_schema(conn: Conn) -> None:
    for table in ("jobs", "companies", "scores"):
        _add_missing_columns(conn, table)
    _create_late_indexes(conn)
    _migrate_applications_status(conn)
    _migrate_contacts_table(conn)
    conn.commit()


def init_schema(conn: Conn) -> None:
    # Tables first, then migrations (add columns), and only then the indexes
    # that reference migrated columns.
    late = {sql for _name, sql in LATE_INDEXES}
    stmts = split_sql(SCHEMA_PG if is_pg(conn) else SCHEMA)
    for stmt in stmts:
        if stmt not in late:
            conn.execute(stmt)
    migrate_schema(conn)
    conn.commit()
