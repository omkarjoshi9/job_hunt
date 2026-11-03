from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from jobdigest.db._common import Conn, RowLike
from jobdigest.paths import repo_root

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

# Tables whose primary key is an ``id`` column (RETURNING id is safe there).
ID_TABLES = frozenset(
    {"companies", "jobs", "scores", "runs", "portal_errors", "applications", "contacts"}
)
_INSERT_TARGET_RE = re.compile(
    r"^\s*INSERT\s+(?:OR\s+\w+\s+)?INTO\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
    re.IGNORECASE,
)


def safe_ident(name: str) -> str:
    """Return ``name`` if it is a plain SQL identifier, else raise ValueError."""
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


class MappingRow:
    def __init__(self, mapping: dict[str, Any]):
        self._data = dict(mapping)
        self._keys = list(mapping.keys())
        self._values = list(mapping.values())

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def keys(self):
        return self._data.keys()


def _adapt_row(row: Any) -> RowLike | None:
    if row is None:
        return None
    if isinstance(row, MappingRow):
        return row
    if isinstance(row, dict):
        return MappingRow(row)
    return row


def _replace_placeholders(sql: str) -> str:
    """Rewrite ``?`` to ``%s`` outside of single-quoted literals / quoted idents."""
    out: list[str] = []
    quote: str | None = None
    for ch in sql:
        if quote is not None:
            if ch == quote:
                quote = None  # doubled quotes re-enter on the next char
            out.append(ch)
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


def _wants_returning_id(sql: str) -> bool:
    if "RETURNING" in sql.upper():
        return False
    match = _INSERT_TARGET_RE.match(sql)
    return bool(match) and match.group(1).lower() in ID_TABLES


def _pg_sql(sql: str) -> str:
    rewritten = _replace_placeholders(sql)
    if _wants_returning_id(rewritten):
        return rewritten.rstrip().rstrip(";") + " RETURNING id"
    return rewritten


class _PgCursor:
    def __init__(self, cur: Any, lastrowid: int | None = None):
        self._cur = cur
        self.lastrowid = lastrowid if lastrowid is not None else 0

    def fetchone(self) -> RowLike | None:
        return _adapt_row(self._cur.fetchone())

    def fetchall(self) -> list[RowLike]:
        return [_adapt_row(row) for row in self._cur.fetchall()]


def split_sql(schema: str) -> list[str]:
    return [part.strip() for part in schema.split(";") if part.strip()]


class _PgConnection:
    def __init__(self, conn: Any):
        self._conn = conn

    def execute(self, sql: str, parameters: Any = ()) -> _PgCursor:
        pg_sql = _pg_sql(sql)
        cur = self._conn.execute(pg_sql, parameters)
        if _wants_returning_id(sql):
            lastrowid = 0
            row = cur.fetchone()
            if row is not None:
                adapted = _adapt_row(row)
                try:
                    lastrowid = int(adapted["id"])
                except (KeyError, TypeError, ValueError):
                    lastrowid = 0
            return _PgCursor(cur, lastrowid=lastrowid)
        return _PgCursor(cur)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def executescript(self, script: str) -> None:
        for stmt in split_sql(script):
            self._conn.execute(stmt)


def is_pg(conn: Any) -> bool:
    return isinstance(conn, _PgConnection)


@contextmanager
def transaction(conn: Conn) -> Iterator[Conn]:
    """Commit once on success, roll back on error (one unit of work = one commit)."""
    try:
        yield conn
    except BaseException:
        try:
            conn.rollback()
        except Exception:  # pragma: no cover - rollback on a dead connection
            pass
        raise
    else:
        conn.commit()


def postgres_url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


def uses_postgres(path: Path | None = None) -> bool:
    return path is None and bool(postgres_url())


def default_db_path() -> Path:
    env = os.environ.get("DATABASE_PATH")
    if env:
        path = Path(env).expanduser()
        if not path.is_absolute():
            path = repo_root() / path
        return path.resolve()
    return (repo_root() / "data" / "jobs.db").resolve()


def table_columns(conn: Conn, table: str) -> set[str]:
    table = safe_ident(table)
    if is_pg(conn):
        rows = conn.execute(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (table,),
        ).fetchall()
        return {str(row["name"]) for row in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names: set[str] = set()
    for row in rows:
        try:
            names.add(str(row["name"]))
        except (KeyError, IndexError, TypeError):
            names.add(str(row[1]))
    return names


def connect_postgres(url: str) -> _PgConnection:
    import psycopg
    from psycopg.rows import dict_row

    from jobdigest.db.schema import init_schema

    raw = psycopg.connect(url, row_factory=dict_row)
    conn = _PgConnection(raw)
    init_schema(conn)
    return conn


def connect(path: Path | None = None) -> Conn:
    from jobdigest.db.schema import init_schema

    if uses_postgres(path):
        return connect_postgres(postgres_url())
    db_path = Path(path) if path is not None else default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    init_schema(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
