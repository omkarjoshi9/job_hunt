import sqlite3

import pytest

from jobdigest.config import load_profile
from jobdigest.db import (
    SCHEMA,
    _pg_sql,
    connect,
    migrate_schema,
    repo_root,
    safe_ident,
    transaction,
    upsert_job,
)
from jobdigest.db.schema import ADDED_COLUMNS
from jobdigest.paths import repo_root as paths_repo_root


def test_repo_root_reexported_from_paths():
    assert repo_root is paths_repo_root
    assert (repo_root() / "pyproject.toml").is_file()


def test_pg_sql_skips_question_marks_in_literals():
    out = _pg_sql("SELECT * FROM jobs WHERE title = 'what?' AND id = ?")
    assert out == "SELECT * FROM jobs WHERE title = 'what?' AND id = %s"
    out = _pg_sql("SELECT 'it''s ?' , ?")
    assert out == "SELECT 'it''s ?' , %s"


def test_pg_sql_returning_only_for_id_tables():
    assert _pg_sql("INSERT INTO runs (kind) VALUES (?)").endswith("RETURNING id")
    assert _pg_sql("insert into jobs (a) values (?);").endswith("RETURNING id")
    assert "RETURNING" not in _pg_sql("INSERT INTO other_table (a) VALUES (?)")
    assert _pg_sql("INSERT INTO runs (k) VALUES (?) RETURNING id").count("RETURNING") == 1
    assert "RETURNING" not in _pg_sql("SELECT 'INSERT INTO runs'")


def test_safe_ident():
    assert safe_ident("jobs") == "jobs"
    for bad in ("jobs; DROP TABLE x", "a-b", "", "1abc", 'a"b'):
        with pytest.raises(ValueError):
            safe_ident(bad)


def test_transaction_commits_and_rolls_back(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO runs (kind, started_at, status) VALUES ('a','t','ok')"
            )
        with pytest.raises(RuntimeError):
            with transaction(conn):
                conn.execute(
                    "INSERT INTO runs (kind, started_at, status) VALUES ('b','t','ok')"
                )
                raise RuntimeError("boom")
        kinds = [r["kind"] for r in conn.execute("SELECT kind FROM runs")]
        assert kinds == ["a"]
    finally:
        conn.close()


def test_upsert_job_single_commit(tmp_path):
    conn = connect(tmp_path / "t.db")
    profile = load_profile()
    commits = []

    class Spy:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def commit(self):
            commits.append(1)
            self._inner.commit()

    spy = Spy(conn)
    try:
        kwargs = dict(
            apply_url="https://example.com/jobs/1",
            hosted_url="",
            source="s",
            external_id="1",
            company_id=None,
            company="Acme",
            title="Software Engineer Intern",
            location_raw="Bengaluru",
            description_text="desc",
            raw_json="{}",
            ats="greenhouse",
        )
        assert upsert_job(spy, profile, **kwargs)[0] == "insert"
        assert len(commits) == 1
        assert upsert_job(spy, profile, **kwargs)[0] == "update"
        assert len(commits) == 2
    finally:
        conn.close()


def test_new_indexes_exist(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    finally:
        conn.close()
    assert {
        "jobs_description_hash",
        "jobs_source_active",
        "jobs_canonical_job_id",
        "jobs_posting_key",
    } <= names
    assert "jobs_description_hash" in SCHEMA


def test_migrates_legacy_jobs_table(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE companies (id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL, ats TEXT NOT NULL, board_token TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 0, resolved_url TEXT, last_fetch_at TEXT,
          last_http_status INTEGER, last_job_count INTEGER, last_error TEXT);
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, apply_url TEXT,
          apply_url_norm TEXT NOT NULL UNIQUE, source TEXT NOT NULL, external_id TEXT,
          company_id INTEGER, company TEXT, title TEXT NOT NULL, location_raw TEXT,
          description_text TEXT, raw_json TEXT NOT NULL, first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
          hard_status TEXT NOT NULL DEFAULT 'pending', hard_reason TEXT,
          ats TEXT NOT NULL DEFAULT '');
        CREATE TABLE scores (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
          model TEXT NOT NULL, prompt_version TEXT NOT NULL, relevant INTEGER NOT NULL,
          score REAL, reasons_json TEXT, created_at TEXT NOT NULL,
          UNIQUE(job_id, model, prompt_version));
        """
    )
    raw.commit()
    raw.close()
    conn = connect(path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        for name, _ddl in ADDED_COLUMNS["jobs"]:
            assert name in cols
        assert "dead_streak" in {
            r["name"] for r in conn.execute("PRAGMA table_info(companies)")
        }
        assert "stale" in {r["name"] for r in conn.execute("PRAGMA table_info(scores)")}
        migrate_schema(conn)  # idempotent
    finally:
        conn.close()
