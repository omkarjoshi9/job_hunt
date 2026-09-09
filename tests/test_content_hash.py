import sqlite3

from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    connect,
    guest_rows,
    unscored_pass_jobs,
    upsert_job,
    upsert_score,
)
from jobdigest.normalize import description_hash


def _intern_kwargs(profile, **overrides):
    kwargs = dict(
        profile=profile,
        apply_url="https://example.com/jobs/hash",
        hosted_url="https://example.com/jobs/hash",
        source="greenhouse",
        external_id="hash-1",
        company_id=None,
        company="Acme",
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="Intern role in Pune",
        raw_json="{}",
        ats="greenhouse",
    )
    kwargs.update(overrides)
    return kwargs


def test_description_hash_collapses_whitespace():
    assert description_hash("a  b") == description_hash("a b")
    assert description_hash("a b") != description_hash("a c")
    assert description_hash(None) == description_hash("")
    assert len(description_hash("x")) == 64


def test_migrate_adds_description_hash_and_stale(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE jobs (
          id INTEGER PRIMARY KEY,
          apply_url TEXT,
          apply_url_norm TEXT NOT NULL UNIQUE,
          source TEXT NOT NULL,
          external_id TEXT,
          company_id INTEGER,
          company TEXT,
          title TEXT NOT NULL,
          location_raw TEXT,
          description_text TEXT,
          raw_json TEXT NOT NULL,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          is_active INTEGER NOT NULL DEFAULT 1,
          hard_status TEXT NOT NULL DEFAULT 'pending',
          hard_reason TEXT,
          ats TEXT NOT NULL DEFAULT '',
          miss_count INTEGER NOT NULL DEFAULT 0,
          closed_at TEXT,
          close_reason TEXT
        );
        CREATE TABLE scores (
          id INTEGER PRIMARY KEY,
          job_id INTEGER NOT NULL,
          model TEXT NOT NULL,
          prompt_version TEXT NOT NULL,
          relevant INTEGER NOT NULL,
          score REAL,
          reasons_json TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(job_id, model, prompt_version)
        );
        INSERT INTO jobs (
          apply_url, apply_url_norm, source, title, raw_json,
          first_seen_at, last_seen_at
        ) VALUES (
          'https://example.com/a', 'https://example.com/a', 'greenhouse',
          'Intern', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        );
        INSERT INTO scores (
          job_id, model, prompt_version, relevant, score, reasons_json, created_at
        ) VALUES (
          1, 'm', 'v1', 1, 80, '{}', '2026-01-01T00:00:00Z'
        );
        """
    )
    raw.close()
    conn = connect(db)
    try:
        job_cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        score_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(scores)").fetchall()
        }
        assert "description_hash" in job_cols
        assert "stale" in score_cols
        row = conn.execute("SELECT stale FROM scores WHERE job_id = 1").fetchone()
        assert int(row["stale"]) == 0
    finally:
        conn.close()


def test_upsert_changed_jd_marks_scores_stale(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    models = load_models()
    try:
        action, jid = upsert_job(conn, **_intern_kwargs(profile))
        assert action == "insert" and jid is not None
        upsert_score(
            conn, jid, models.score_model, models.score_prompt_version, True, 90.0, {}
        )
        action2, jid2 = upsert_job(
            conn,
            **_intern_kwargs(
                profile, description_text="Intern role in Pune. US work authorization required."
            ),
        )
        assert action2 == "update" and jid2 == jid
        row = conn.execute(
            "SELECT stale FROM scores WHERE job_id = ?", (jid,)
        ).fetchone()
        assert int(row["stale"]) == 1
    finally:
        conn.close()


def test_upsert_unchanged_jd_does_not_stale(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    models = load_models()
    try:
        _, jid = upsert_job(conn, **_intern_kwargs(profile))
        upsert_score(
            conn, jid, models.score_model, models.score_prompt_version, True, 90.0, {}
        )
        upsert_job(conn, **_intern_kwargs(profile, description_text="Intern  role  in Pune"))
        row = conn.execute(
            "SELECT stale FROM scores WHERE job_id = ?", (jid,)
        ).fetchone()
        assert int(row["stale"]) == 0
    finally:
        conn.close()


def test_first_hash_populate_does_not_stale(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE jobs (
          id INTEGER PRIMARY KEY,
          apply_url TEXT,
          apply_url_norm TEXT NOT NULL UNIQUE,
          source TEXT NOT NULL,
          external_id TEXT,
          company_id INTEGER,
          company TEXT,
          title TEXT NOT NULL,
          location_raw TEXT,
          description_text TEXT,
          raw_json TEXT NOT NULL,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          is_active INTEGER NOT NULL DEFAULT 1,
          hard_status TEXT NOT NULL DEFAULT 'pending',
          hard_reason TEXT,
          ats TEXT NOT NULL DEFAULT '',
          miss_count INTEGER NOT NULL DEFAULT 0,
          closed_at TEXT,
          close_reason TEXT
        );
        CREATE TABLE scores (
          id INTEGER PRIMARY KEY,
          job_id INTEGER NOT NULL,
          model TEXT NOT NULL,
          prompt_version TEXT NOT NULL,
          relevant INTEGER NOT NULL,
          score REAL,
          reasons_json TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(job_id, model, prompt_version)
        );
        INSERT INTO jobs (
          apply_url, apply_url_norm, source, external_id, company, title,
          location_raw, description_text, raw_json, first_seen_at, last_seen_at,
          hard_status, ats
        ) VALUES (
          'https://example.com/jobs/hash', 'https://example.com/jobs/hash',
          'greenhouse', 'hash-1', 'Acme', 'Software Engineer Intern',
          'Pune', 'Intern role in Pune', '{}',
          '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
          'pass', 'greenhouse'
        );
        INSERT INTO scores (
          job_id, model, prompt_version, relevant, score, reasons_json, created_at
        ) VALUES (1, 'm', 'v1', 1, 80, '{}', '2026-01-01T00:00:00Z');
        """
    )
    raw.close()
    conn = connect(db)
    profile = load_profile()
    try:
        upsert_job(conn, **_intern_kwargs(profile))
        row = conn.execute("SELECT stale FROM scores WHERE job_id = 1").fetchone()
        assert int(row["stale"]) == 0
        hashed = conn.execute(
            "SELECT description_hash FROM jobs WHERE id = 1"
        ).fetchone()
        assert hashed["description_hash"]
    finally:
        conn.close()


def test_unscored_pass_jobs_includes_stale(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    models = load_models()
    try:
        _, jid = upsert_job(conn, **_intern_kwargs(profile))
        upsert_score(
            conn, jid, models.score_model, models.score_prompt_version, True, 90.0, {}
        )
        fresh = unscored_pass_jobs(
            conn, models.score_model, models.score_prompt_version
        )
        assert fresh == []
        upsert_job(
            conn,
            **_intern_kwargs(profile, description_text="Changed JD in Pune intern"),
        )
        stale = unscored_pass_jobs(
            conn, models.score_model, models.score_prompt_version
        )
        assert [int(r["id"]) for r in stale] == [jid]
    finally:
        conn.close()


def test_guest_rows_skips_stale_relevant(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    models = load_models()
    try:
        _, jid = upsert_job(conn, **_intern_kwargs(profile))
        upsert_score(
            conn,
            jid,
            models.score_model,
            models.score_prompt_version,
            True,
            90.0,
            {"why": "fit"},
        )
        guests = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=profile,
        )
        assert [int(g["id"]) for g in guests] == [jid]
        upsert_job(
            conn,
            **_intern_kwargs(profile, description_text="Changed JD in Pune intern"),
        )
        guests2 = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=profile,
        )
        assert guests2 == []
    finally:
        conn.close()


def test_upsert_score_clears_stale(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    models = load_models()
    try:
        _, jid = upsert_job(conn, **_intern_kwargs(profile))
        upsert_score(
            conn, jid, models.score_model, models.score_prompt_version, True, 90.0, {}
        )
        upsert_job(
            conn,
            **_intern_kwargs(profile, description_text="Changed JD in Pune intern"),
        )
        upsert_score(
            conn, jid, models.score_model, models.score_prompt_version, True, 88.0, {}
        )
        row = conn.execute(
            "SELECT stale, score FROM scores WHERE job_id = ?", (jid,)
        ).fetchone()
        assert int(row["stale"]) == 0
        assert float(row["score"]) == 88.0
        guests = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=profile,
        )
        assert [int(g["id"]) for g in guests] == [jid]
    finally:
        conn.close()
