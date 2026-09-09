from jobdigest.config import load_profile
from jobdigest.db import (
    SCHEMA,
    SCHEMA_PG,
    connect,
    contacts_for_job,
    best_contact_for_job,
    last_digest_job_ids,
    last_ok_run,
    migrate_schema,
    upsert_contact,
    upsert_job,
    finish_run,
    insert_run,
)


def _plant_job(conn, *, url="https://example.com/jobs/c1", company="Acme Inc."):
    profile = load_profile()
    action, jid = upsert_job(
        conn,
        profile,
        apply_url=url,
        hosted_url=url,
        source="greenhouse",
        external_id=url.rsplit("/", 1)[-1],
        company_id=None,
        company=company,
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="Intern role in Pune",
        raw_json="{}",
        ats="greenhouse",
        employment_hint="intern",
    )
    assert action == "insert" and jid is not None
    return jid


def test_upsert_contact_insert(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        jid = _plant_job(conn)
        action, cid = upsert_contact(
            conn,
            job_id=jid,
            company="Acme Inc.",
            name="Jane Doe",
            source="jd",
            email="Jane@Acme.COM",
            role="Hiring Manager",
            domain="acme.com",
            confidence=0.9,
        )
        assert action == "insert"
        assert cid is not None
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (cid,)).fetchone()
        assert row["verified"] == "unverified"
        assert row["email"] == "jane@acme.com"
        assert row["created_at"]
        assert row["company"]
    finally:
        conn.close()


def test_upsert_contact_dedup_same_job_name_source(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        jid = _plant_job(conn)
        _, cid1 = upsert_contact(
            conn,
            job_id=jid,
            company="Acme",
            name="Jane Doe",
            source="jd",
            email="jane@acme.com",
            confidence=0.4,
        )
        action, cid2 = upsert_contact(
            conn,
            job_id=jid,
            company="Acme",
            name="Jane Doe",
            source="jd",
            email="jane@acme.com",
            role="Recruiter",
            confidence=0.8,
        )
        assert action == "update"
        assert cid1 == cid2
        count = conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"]
        assert count == 1
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (cid2,)).fetchone()
        assert row["role"] == "Recruiter"
        assert float(row["confidence"]) == 0.8
    finally:
        conn.close()


def test_upsert_contact_keeps_higher_confidence_on_conflict(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        jid = _plant_job(conn)
        upsert_contact(
            conn,
            job_id=jid,
            company="Acme",
            name="Jane Doe",
            source="jd",
            email="jane@acme.com",
            confidence=0.9,
        )
        upsert_contact(
            conn,
            job_id=jid,
            company="Acme",
            name="Jane Doe",
            source="jd",
            email="other@acme.com",
            confidence=0.2,
        )
        row = conn.execute("SELECT email, confidence FROM contacts").fetchone()
        assert float(row["confidence"]) == 0.9
        assert row["email"] == "other@acme.com"
    finally:
        conn.close()


def test_upsert_contact_skips_blank_name(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        jid = _plant_job(conn)
        assert upsert_contact(
            conn, job_id=jid, company="Acme", name="  ", source="jd"
        ) == ("skip", None)
        count = conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"]
        assert count == 0
    finally:
        conn.close()


def test_upsert_contact_skips_invalid_source(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        jid = _plant_job(conn)
        assert upsert_contact(
            conn,
            job_id=jid,
            company="Acme",
            name="Jane Doe",
            source="linkedin",
        ) == ("skip", None)
    finally:
        conn.close()


def test_contacts_for_job_and_best_contact(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        jid = _plant_job(conn)
        other = _plant_job(conn, url="https://example.com/jobs/c2", company="Other")
        upsert_contact(
            conn,
            job_id=jid,
            company="Acme",
            name="Pat Pattern",
            source="pattern",
            email="pat@acme.com",
            confidence=0.5,
        )
        upsert_contact(
            conn,
            job_id=jid,
            company="Acme",
            name="Jane Doe",
            source="jd",
            email="jane@acme.com",
            confidence=0.4,
        )
        upsert_contact(
            conn,
            job_id=other,
            company="Other",
            name="Skip Me",
            source="jd",
            email="skip@other.com",
            confidence=1.0,
        )
        rows = contacts_for_job(conn, jid)
        assert [r["name"] for r in rows] == ["Jane Doe", "Pat Pattern"]
        best = best_contact_for_job(conn, jid)
        assert best["name"] == "Jane Doe"
        assert best_contact_for_job(conn, 99999) is None
    finally:
        conn.close()


def test_best_contact_prefers_email_then_jd_source(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        jid = _plant_job(conn)
        upsert_contact(
            conn,
            job_id=jid,
            company="Acme",
            name="No Email",
            source="jd",
            confidence=0.99,
        )
        upsert_contact(
            conn,
            job_id=jid,
            company="Acme",
            name="Has Email",
            source="github",
            email="talent@acme.com",
            confidence=0.2,
        )
        best = best_contact_for_job(conn, jid)
        assert best["name"] == "Has Email"
    finally:
        conn.close()


def test_sqlite_db_path_ignores_database_url_for_contacts(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    conn = connect(tmp_path / "x.db")
    try:
        jid = _plant_job(conn)
        _, cid = upsert_contact(
            conn, job_id=jid, company="Acme", name="Jane", source="jd"
        )
        assert cid is not None
        assert contacts_for_job(conn, jid)
    finally:
        conn.close()


def test_migrate_schema_creates_contacts_when_table_missing(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        conn.execute("DROP TABLE contacts")
        conn.commit()
        migrate_schema(conn)
        cols = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(contacts)").fetchall()
        }
        assert "email" in cols
        assert "source" in cols
        jid = _plant_job(conn)
        action, _ = upsert_contact(
            conn, job_id=jid, company="Acme", name="Jane", source="jd"
        )
        assert action == "insert"
    finally:
        conn.close()


def test_last_digest_job_ids(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        assert last_digest_job_ids(conn) == []
        run_id = insert_run(conn, "digest")
        finish_run(conn, run_id, "ok", {"guest": 2, "path": "digest", "job_ids": [3, 9]})
        assert last_ok_run(conn, "digest")["id"] == run_id
        assert last_digest_job_ids(conn) == [3, 9]
    finally:
        conn.close()


def test_schema_includes_contacts():
    assert "CREATE TABLE IF NOT EXISTS contacts" in SCHEMA
    assert "UNIQUE(job_id, name, source)" in SCHEMA
    assert "CHECK (source IN ('jd','github','pattern'))" in SCHEMA
    assert "contacts_job_id" in SCHEMA
    assert "CREATE TABLE IF NOT EXISTS contacts" in SCHEMA_PG
    assert "UNIQUE(job_id, name, source)" in SCHEMA_PG
    assert "GENERATED BY DEFAULT AS IDENTITY" in SCHEMA_PG
