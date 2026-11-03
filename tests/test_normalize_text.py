from jobdigest.db import connect, upsert_job
from jobdigest.config import load_profile
from jobdigest.normalize import normalize_company, normalize_location, normalize_title


def test_normalize_company_strips_legal_suffix():
    assert normalize_company("Razorpay Software Private Limited") == "Razorpay"
    assert normalize_company("Razorpay") == "Razorpay"
    assert normalize_company("Razorpay Pvt. Ltd.") == "Razorpay"
    assert normalize_company("Razorpay Software Private Limited") == normalize_company(
        "Razorpay"
    )


def test_normalize_company_ampersand():
    assert normalize_company("Foo & Bar LLP") == "Foo and Bar"


def test_normalize_company_google_cloud_india_distinct():
    assert normalize_company("Google") == "Google"
    assert normalize_company("Google Cloud India") == "Google Cloud India"
    assert normalize_company("Google") != normalize_company("Google Cloud India")


def test_normalize_title_strips_city_paren_year_campus():
    assert normalize_title("Software Engineer (Bengaluru)") == "Software Engineer"
    assert normalize_title("SDE Intern - Pune") == "SDE Intern"
    assert normalize_title("Intern 2026") == "Intern"
    assert normalize_title("Campus Intern") == "Intern"
    assert normalize_title("Off-campus Software Engineer Intern") == (
        "Software Engineer Intern"
    )


def test_normalize_title_keeps_intern():
    assert "Intern" in normalize_title("Software Engineer Intern (Bengaluru)")


def test_normalize_location_bangalore_alias():
    assert normalize_location("Bangalore") == "bengaluru"
    assert normalize_location("Bengaluru | Karnataka") == "bengaluru"
    assert normalize_location("Gurgaon") == "gurugram"


def test_upsert_stores_raw_and_normalized_title_company(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        action, jid = upsert_job(
            conn,
            profile,
            apply_url="https://example.com/jobs/norm",
            hosted_url="",
            source="greenhouse",
            external_id="n1",
            company_id=None,
            company="Razorpay Software Private Limited",
            title="Software Engineer Intern (Bengaluru)",
            location_raw="Bengaluru",
            description_text="Intern role in Bengaluru",
            raw_json="{}",
            ats="greenhouse",
        )
        assert action == "insert" and jid is not None
        row = conn.execute(
            "SELECT title, company, raw_title, raw_company, hard_status FROM jobs"
        ).fetchone()
        assert row["raw_company"] == "Razorpay Software Private Limited"
        assert row["raw_title"] == "Software Engineer Intern (Bengaluru)"
        assert row["company"] == "Razorpay"
        assert row["title"] == "Software Engineer Intern"
        assert row["hard_status"] == "pass"
    finally:
        conn.close()


def test_hard_filter_uses_raw_campus_title(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, jid = upsert_job(
            conn,
            profile,
            apply_url="https://example.com/jobs/campus",
            hosted_url="",
            source="greenhouse",
            external_id="c1",
            company_id=None,
            company="Acme",
            title="Campus Hire Software Engineer",
            location_raw="Pune",
            description_text="Campus hire in Pune India intern software",
            raw_json="{}",
            ats="greenhouse",
        )
        row = conn.execute(
            "SELECT title, raw_title, hard_status FROM jobs WHERE id = ?",
            (jid,),
        ).fetchone()
        assert row["raw_title"] == "Campus Hire Software Engineer"
        assert row["title"] == "Hire Software Engineer"
        assert row["hard_status"] == "pass"
    finally:
        conn.close()
