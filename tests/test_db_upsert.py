from jobdigest.config import load_profile
from jobdigest.db import connect, upsert_job
import sqlite3


def test_foreign_keys_on(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert int(row[0]) == 1
    finally:
        conn.close()


def test_utm_pair_is_one_row(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    kwargs = dict(
        profile=profile,
        hosted_url="",
        source="greenhouse",
        external_id="1",
        company_id=None,
        company="X",
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="Pune intern",
        raw_json="{}",
        ats="greenhouse",
    )
    try:
        action1, id1 = upsert_job(conn, apply_url="https://x.com/a?utm_source=1", **kwargs)
        conn.execute("UPDATE jobs SET last_seen_at = ?", ("2000-01-01T00:00:00Z",))
        conn.commit()
        action2, id2 = upsert_job(conn, apply_url="https://x.com/a", **kwargs)
        count = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        row = conn.execute("SELECT last_seen_at FROM jobs WHERE id = ?", (id2,)).fetchone()
        assert action1 == "insert"
        assert action2 == "update"
        assert id1 == id2
        assert count == 1
        assert row["last_seen_at"] != "2000-01-01T00:00:00Z"
    finally:
        conn.close()


def test_url_less_empty_id_does_not_collide(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    kwargs = dict(
        profile=profile,
        apply_url="",
        hosted_url="",
        source="lever",
        external_id="",
        company_id=None,
        company="X",
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="Pune intern",
        raw_json="{}",
        ats="lever",
    )
    try:
        assert upsert_job(conn, **kwargs) == ("skip", None)
        assert upsert_job(conn, **kwargs) == ("skip", None)
        count = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        assert count == 0
    finally:
        conn.close()


def _intern_kwargs(profile, **overrides):
    kwargs = dict(
        profile=profile,
        apply_url="https://example.com/jobs/hint",
        hosted_url="https://example.com/jobs/hint",
        source="lever",
        external_id="hint-1",
        company_id=None,
        company="Example Corp",
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="Intern role in Pune",
        raw_json="{}",
        ats="lever",
    )
    kwargs.update(overrides)
    return kwargs


def test_upsert_persists_employment_and_workplace_hints(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        action, jid = upsert_job(
            conn,
            **_intern_kwargs(
                profile, employment_hint="Intern", workplace_hint="hybrid"
            ),
        )
        assert action == "insert" and jid is not None
        row = conn.execute(
            "SELECT employment_hint, workplace_hint FROM jobs WHERE id = ?",
            (jid,),
        ).fetchone()
        assert row["employment_hint"] == "Intern"
        assert row["workplace_hint"] == "hybrid"
    finally:
        conn.close()


def test_upsert_overwrites_hints_with_null(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        action, jid = upsert_job(
            conn,
            **_intern_kwargs(
                profile, employment_hint="Intern", workplace_hint="hybrid"
            ),
        )
        assert action == "insert" and jid is not None
        action2, jid2 = upsert_job(
            conn,
            **_intern_kwargs(profile, employment_hint=None, workplace_hint=None),
        )
        assert action2 == "update" and jid2 == jid
        row = conn.execute(
            "SELECT employment_hint, workplace_hint FROM jobs WHERE id = ?",
            (jid,),
        ).fetchone()
        assert row["employment_hint"] is None
        assert row["workplace_hint"] is None
    finally:
        conn.close()


def test_upsert_caps_hint_at_80_chars(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        action, jid = upsert_job(
            conn,
            **_intern_kwargs(
                profile,
                employment_hint="I" * 200,
                workplace_hint="Intern\n\t  Hybrid",
            ),
        )
        assert action == "insert" and jid is not None
        row = conn.execute(
            "SELECT employment_hint, workplace_hint FROM jobs WHERE id = ?",
            (jid,),
        ).fetchone()
        assert row["employment_hint"] == "I" * 80
        assert row["workplace_hint"] == "Intern Hybrid"
        assert "\n" not in row["workplace_hint"]
    finally:
        conn.close()


def test_foss_rss_and_portal_same_url_is_one_row(tmp_path):
    """Same apply URL seen via the HTML portal AND the RSS feed collapses to one DB row."""
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    apply_url = "https://fossunited.org/jobs/senior-python-dev"
    base_kwargs = dict(
        profile=profile,
        apply_url=apply_url,
        hosted_url=apply_url,
        external_id="",
        company_id=None,
        company="FOSS United",
        title="Senior Python Developer",
        location_raw="Remote",
        description_text="Python dev role posted on FOSS United",
        raw_json="{}",
    )
    try:
        action1, id1 = upsert_job(conn, source="foss-united", ats="portal", **base_kwargs)
        action2, id2 = upsert_job(conn, source="foss-united-rss", ats="rss", **base_kwargs)
        count = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        assert action1 == "insert"
        assert action2 == "update"
        assert id1 == id2
        assert count == 1
    finally:
        conn.close()


def test_migrate_adds_employment_workplace_hint_columns(tmp_path):
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
        INSERT INTO jobs (
          apply_url, apply_url_norm, source, title, raw_json,
          first_seen_at, last_seen_at
        ) VALUES (
          'https://example.com/a', 'https://example.com/a', 'greenhouse',
          'Intern', '{}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        );
        """
    )
    raw.close()
    conn = connect(db)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "employment_hint" in cols
        assert "workplace_hint" in cols
        row = conn.execute(
            "SELECT employment_hint, workplace_hint FROM jobs"
        ).fetchone()
        assert row["employment_hint"] is None
        assert row["workplace_hint"] is None
    finally:
        conn.close()
