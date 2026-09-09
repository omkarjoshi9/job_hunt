from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    bump_unseen_for_source,
    connect,
    guest_rows,
    unscored_pass_jobs,
    upsert_application,
    upsert_job,
    upsert_score,
)
from jobdigest.normalize import posting_key


def _intern(profile, **overrides):
    kwargs = dict(
        profile=profile,
        hosted_url="",
        company_id=None,
        company="Razorpay Software Private Limited",
        title="Software Engineer Intern (Bengaluru)",
        location_raw="Bengaluru",
        description_text="Intern role in Bengaluru",
        raw_json="{}",
    )
    kwargs.update(overrides)
    return kwargs


def test_posting_key_razorpay_alias():
    a = posting_key(
        "Software Engineer Intern",
        "Razorpay Software Private Limited",
        "Bengaluru",
    )
    b = posting_key("Software Engineer Intern", "Razorpay", "Bangalore")
    assert a and b and a == b


def test_posting_key_google_cloud_distinct():
    a = posting_key("Software Engineer Intern", "Google", "Bengaluru")
    b = posting_key("Software Engineer Intern", "Google Cloud India", "Bengaluru")
    assert a and b and a != b


def test_ats_portal_two_urls_one_guest(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    models = load_models()
    try:
        _, portal_id = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://hasjob.example/r1",
                source="hasjob",
                external_id="p1",
                ats="portal",
            ),
        )
        _, ats_id = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://boards.greenhouse.io/razorpay/jobs/1",
                source="razorpay",
                external_id="g1",
                ats="greenhouse",
            ),
        )
        assert portal_id != ats_id
        portal = conn.execute(
            "SELECT canonical_job_id FROM jobs WHERE id = ?", (portal_id,)
        ).fetchone()
        ats = conn.execute(
            "SELECT canonical_job_id FROM jobs WHERE id = ?", (ats_id,)
        ).fetchone()
        assert ats["canonical_job_id"] is None
        assert int(portal["canonical_job_id"]) == ats_id
        upsert_score(
            conn, ats_id, models.score_model, models.score_prompt_version, True, 90.0, {}
        )
        upsert_score(
            conn,
            portal_id,
            models.score_model,
            models.score_prompt_version,
            True,
            91.0,
            {},
        )
        guests = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=profile,
        )
        assert [int(g["id"]) for g in guests] == [ats_id]
    finally:
        conn.close()


def test_ats_wins_even_if_portal_inserted_first(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, portal_id = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://foss.example/1",
                source="foss-united",
                external_id="f1",
                ats="portal",
            ),
        )
        _, ats_id = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://jobs.lever.co/razorpay/abc",
                source="razorpay",
                external_id="l1",
                ats="lever",
            ),
        )
        portal = conn.execute(
            "SELECT canonical_job_id FROM jobs WHERE id = ?", (portal_id,)
        ).fetchone()
        ats = conn.execute(
            "SELECT canonical_job_id FROM jobs WHERE id = ?", (ats_id,)
        ).fetchone()
        assert ats["canonical_job_id"] is None
        assert int(portal["canonical_job_id"]) == ats_id
    finally:
        conn.close()


def test_agency_different_company_not_merged(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, a = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://a.example/1",
                source="greenhouse",
                external_id="1",
                ats="greenhouse",
                company="Razorpay",
            ),
        )
        _, b = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://b.example/2",
                source="agency",
                external_id="2",
                ats="portal",
                company="HireFast Staffing",
            ),
        )
        row_a = conn.execute(
            "SELECT canonical_job_id FROM jobs WHERE id = ?", (a,)
        ).fetchone()
        row_b = conn.execute(
            "SELECT canonical_job_id FROM jobs WHERE id = ?", (b,)
        ).fetchone()
        assert row_a["canonical_job_id"] is None
        assert row_b["canonical_job_id"] is None
    finally:
        conn.close()


def test_application_on_alias_suppresses_canonical_guest(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    models = load_models()
    try:
        _, portal_id = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://portal.example/r",
                source="hasjob",
                external_id="p",
                ats="portal",
            ),
        )
        _, ats_id = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://ats.example/r",
                source="razorpay",
                external_id="g",
                ats="greenhouse",
            ),
        )
        upsert_score(
            conn, ats_id, models.score_model, models.score_prompt_version, True, 90.0, {}
        )
        upsert_application(
            conn, apply_url="https://portal.example/r", status="applied"
        )
        guests = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=profile,
        )
        assert guests == []
    finally:
        conn.close()


def test_unscored_skips_aliases(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    models = load_models()
    try:
        _, portal_id = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://portal.example/u",
                source="hasjob",
                external_id="pu",
                ats="portal",
            ),
        )
        _, ats_id = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://ats.example/u",
                source="razorpay",
                external_id="gu",
                ats="greenhouse",
            ),
        )
        ids = [
            int(r["id"])
            for r in unscored_pass_jobs(
                conn, models.score_model, models.score_prompt_version
            )
        ]
        assert ats_id in ids
        assert portal_id not in ids
    finally:
        conn.close()


def test_board_error_still_does_not_increment_miss_count(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, jid = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://ats.example/miss",
                source="example",
                external_id="m1",
                ats="greenhouse",
            ),
        )
        stats = bump_unseen_for_source(conn, "example", set(), success=False)
        assert stats == {"missed": 0, "tombstoned": 0}
        row = conn.execute(
            "SELECT miss_count, is_active FROM jobs WHERE id = ?", (jid,)
        ).fetchone()
        assert int(row["miss_count"]) == 0
        assert int(row["is_active"]) == 1
    finally:
        conn.close()
