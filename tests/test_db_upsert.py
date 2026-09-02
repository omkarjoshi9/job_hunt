from jobdigest.config import load_profile
from jobdigest.db import connect, upsert_job


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
