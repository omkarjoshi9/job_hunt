from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    MIN_OUTCOMES,
    connect,
    guest_rows,
    load_company_priors,
    relevant_unapplied_jobs,
    upsert_application,
    upsert_job,
    upsert_score,
)
from jobdigest.normalize import normalize_company
from jobdigest.rank import (
    LOC_HOME,
    PRIOR_MAX,
    PRIOR_POSITIVE,
    RECENCY_MAX,
    blend_rank,
    company_prior,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
HOME = "Pune"
RELOCATE = ["Bengaluru"]


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _profile():
    return replace(load_profile(), location="Pune", relocate=["Bengaluru"])


def _company_key(name: str) -> str:
    return normalize_company(name).casefold()


def _blend_row(*, company: str, score: float, first_seen_at: str, location_raw: str):
    return {
        "id": 1,
        "company": company,
        "score": score,
        "first_seen_at": first_seen_at,
        "location_raw": location_raw,
        "title": "Software Engineer Intern",
    }


def _plant_outcomes(conn, n, *, status="rejected", company="Filler", ns="outcome"):
    _ = ns
    for i in range(n):
        label = f"{company}-{i:02d}" if company == "Filler" else company
        upsert_application(
            conn,
            apply_url=f"https://example.com/job-{i}",
            company=label,
            title="Intern",
            status=status,
        )


def _app(conn, slug: str, *, company: str, status: str):
    upsert_application(
        conn,
        apply_url=f"https://priors.test/extra/{slug}",
        company=company,
        title="Intern",
        status=status,
    )


def _relevant_job(
    conn,
    *,
    url: str,
    company: str,
    location_raw: str = "Pune",
    score: float = 80.0,
    first_seen_at: str | None = None,
    company_id=None,
    source: str = "greenhouse",
    ats: str = "greenhouse",
    title: str = "Software Engineer Intern",
):
    profile = load_profile()
    models = load_models()
    action, jid = upsert_job(
        conn,
        profile,
        apply_url=url,
        hosted_url=url,
        source=source,
        external_id=url.rsplit("/", 1)[-1],
        company_id=company_id,
        company=company,
        title=title,
        location_raw=location_raw,
        description_text="Intern role in Pune",
        raw_json="{}",
        ats=ats,
        employment_hint="intern",
    )
    assert action == "insert" and jid is not None
    upsert_score(
        conn,
        jid,
        models.score_model,
        models.score_prompt_version,
        True,
        score,
        {"why": "fit"},
    )
    if first_seen_at is not None:
        conn.execute(
            "UPDATE jobs SET first_seen_at = ? WHERE id = ?", (first_seen_at, jid)
        )
        conn.commit()
    return jid


def _guest_ids(conn, *, digest_max=12, now=NOW):
    models = load_models()
    rows = guest_rows(
        conn,
        models.score_model,
        models.score_prompt_version,
        digest_max,
        profile=_profile(),
        now=now,
    )
    return [int(r["id"]) for r in rows]


def test_company_prior_oa_is_five():
    priors = {_company_key("Acme"): PRIOR_POSITIVE}
    assert company_prior({"company": "Acme"}, priors) == PRIOR_POSITIVE
    assert (
        company_prior({"company": "Other", "display_name": "Acme"}, priors)
        == PRIOR_POSITIVE
    )


def test_company_prior_interview_and_offer_do_not_stack(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        _plant_outcomes(conn, 18)
        _app(conn, "interview", company="Acme", status="interview")
        _app(conn, "offer", company="Acme", status="offer")
        priors = load_company_priors(conn)
        key = _company_key("Acme")
        assert priors == {key: PRIOR_POSITIVE}
        row = _blend_row(
            company="Acme",
            score=80,
            first_seen_at=_iso(NOW - timedelta(days=10)),
            location_raw="Mumbai",
        )
        assert blend_rank(
            row, home=HOME, relocate=RELOCATE, now=NOW, priors=priors
        ) == pytest.approx(80 + PRIOR_POSITIVE)
    finally:
        conn.close()


def test_company_prior_saved_applied_hidden_are_zero(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        _plant_outcomes(conn, 19, status="oa")
        _app(conn, "saved", company="Acme", status="saved")
        _app(conn, "applied", company="Acme", status="applied")
        _app(conn, "hidden", company="Acme", status="hidden")
        assert load_company_priors(conn) == {}
        _app(conn, "oa-20", company="Filler-19", status="oa")
        priors = load_company_priors(conn)
        assert _company_key("Acme") not in priors
        assert company_prior({"company": "Acme"}, priors) == 0.0
        assert priors[_company_key("Filler-00")] == PRIOR_POSITIVE
    finally:
        conn.close()


def test_company_prior_rejected_ghosted_are_zero_v1(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        _plant_outcomes(conn, 18)
        _app(conn, "rejected", company="Acme", status="rejected")
        _app(conn, "ghosted", company="Acme", status="ghosted")
        priors = load_company_priors(conn)
        assert priors == {}
        assert company_prior({"company": "Acme"}, priors) == 0.0
    finally:
        conn.close()


def test_company_prior_missing_company_is_zero():
    priors = {_company_key("Acme"): PRIOR_POSITIVE}
    assert company_prior({"company": ""}, priors) == 0.0
    assert company_prior({}, priors) == 0.0
    assert company_prior({"company": "Acme"}, {}) == 0.0


def test_blend_prior_does_not_exceed_prior_max():
    row = _blend_row(
        company="Acme",
        score=80,
        first_seen_at=_iso(NOW),
        location_raw="Pune",
    )
    priors = {_company_key("Acme"): 50.0}
    assert company_prior(row, priors) == PRIOR_MAX
    assert blend_rank(
        row, home=HOME, relocate=RELOCATE, now=NOW, priors=priors
    ) == pytest.approx(80 + RECENCY_MAX + LOC_HOME + PRIOR_MAX)


def test_recency_still_outranks_prior_on_equal_llm_equal_city():
    fresh = _blend_row(
        company="FreshCo",
        score=80,
        first_seen_at=_iso(NOW),
        location_raw="Pune",
    )
    stale = _blend_row(
        company="PriorCo",
        score=80,
        first_seen_at=_iso(NOW - timedelta(days=10)),
        location_raw="Pune",
    )
    priors = {_company_key("PriorCo"): PRIOR_POSITIVE}
    rank_fresh = blend_rank(fresh, home=HOME, relocate=RELOCATE, now=NOW, priors=priors)
    rank_stale = blend_rank(stale, home=HOME, relocate=RELOCATE, now=NOW, priors=priors)
    assert rank_fresh == pytest.approx(80 + RECENCY_MAX + LOC_HOME)
    assert rank_stale == pytest.approx(80 + LOC_HOME + PRIOR_POSITIVE)
    assert RECENCY_MAX > PRIOR_POSITIVE
    assert rank_fresh > rank_stale


def test_load_company_priors_gate_below_20_is_empty(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        assert MIN_OUTCOMES == 20
        _plant_outcomes(conn, 19, status="oa", company="Acme")
        assert load_company_priors(conn) == {}
    finally:
        conn.close()


def test_load_company_priors_gate_at_20_enables_bonus(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        _plant_outcomes(conn, 20, status="oa", company="BonusCo")
        priors = load_company_priors(conn)
        assert priors[_company_key("BonusCo")] == PRIOR_POSITIVE
        assert 0.0 not in priors.values()
    finally:
        conn.close()


def test_guest_rows_oa_company_outranks_equal_blend_other_company(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        seen = _iso(NOW)
        _plant_outcomes(conn, 19)
        _app(conn, "oa", company="PriorCo", status="oa")
        other = _relevant_job(
            conn,
            url="https://priors.test/jobs/other",
            company="OtherCo",
            score=80.0,
            first_seen_at=seen,
        )
        prior_guest = _relevant_job(
            conn,
            url="https://priors.test/jobs/prior",
            company="PriorCo",
            score=80.0,
            first_seen_at=seen,
        )
        assert _guest_ids(conn)[:2] == [prior_guest, other]
    finally:
        conn.close()


def test_guest_rows_prior_does_not_surface_hard_reject(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        _plant_outcomes(conn, 19)
        _app(conn, "oa", company="RejectCo", status="oa")
        rejected = _relevant_job(
            conn,
            url="https://priors.test/jobs/reject",
            company="RejectCo",
            score=99.0,
            first_seen_at=_iso(NOW),
        )
        conn.execute(
            "UPDATE jobs SET hard_status = 'reject', hard_reason = 'yoe' WHERE id = ?",
            (rejected,),
        )
        conn.commit()
        ok = _relevant_job(
            conn,
            url="https://priors.test/jobs/ok",
            company="OkCo",
            score=70.0,
            first_seen_at=_iso(NOW),
        )
        ids = _guest_ids(conn)
        assert rejected not in ids
        assert ok in ids
    finally:
        conn.close()


def test_guest_rows_applied_url_still_excluded_even_if_company_has_oa(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        _plant_outcomes(conn, 19)
        _app(conn, "oa", company="Acme", status="oa")
        applied = _relevant_job(
            conn,
            url="https://priors.test/jobs/applied",
            company="Acme",
            score=90.0,
            first_seen_at=_iso(NOW),
        )
        upsert_application(
            conn,
            apply_url="https://priors.test/jobs/applied",
            company="Acme",
            status="applied",
        )
        guest = _relevant_job(
            conn,
            url="https://priors.test/jobs/guest",
            company="Acme",
            score=80.0,
            first_seen_at=_iso(NOW),
            title="Backend Engineer Intern",
        )
        ids = _guest_ids(conn)
        assert applied not in ids
        assert guest in ids
    finally:
        conn.close()


def test_normalize_company_joins_legal_suffix(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        seen = _iso(NOW)
        _plant_outcomes(conn, 19)
        _app(
            conn,
            "oa",
            company="Razorpay Software Private Limited",
            status="oa",
        )
        other = _relevant_job(
            conn,
            url="https://priors.test/jobs/other",
            company="OtherCo",
            score=80.0,
            first_seen_at=seen,
        )
        razor = _relevant_job(
            conn,
            url="https://priors.test/jobs/razor",
            company="Razorpay",
            score=80.0,
            first_seen_at=seen,
        )
        priors = load_company_priors(conn)
        assert priors[_company_key("Razorpay")] == PRIOR_POSITIVE
        assert _guest_ids(conn)[:2] == [razor, other]
    finally:
        conn.close()


def test_google_not_merged_with_google_cloud_india(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        seen = _iso(NOW)
        _plant_outcomes(conn, 19)
        _app(conn, "oa", company="Google", status="oa")
        cloud = _relevant_job(
            conn,
            url="https://priors.test/jobs/cloud",
            company="Google Cloud India",
            score=80.0,
            first_seen_at=seen,
        )
        google = _relevant_job(
            conn,
            url="https://priors.test/jobs/google",
            company="Google",
            score=80.0,
            first_seen_at=seen,
        )
        priors = load_company_priors(conn)
        assert _company_key("Google") in priors
        assert _company_key("Google Cloud India") not in priors
        assert _guest_ids(conn)[:2] == [google, cloud]
    finally:
        conn.close()


def test_portal_job_without_company_id_still_matches_on_jobs_company(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        seen = _iso(NOW)
        _plant_outcomes(conn, 19)
        oa_job = _relevant_job(
            conn,
            url="https://priors.test/jobs/portal-oa",
            company="PortalCo",
            score=80.0,
            first_seen_at=seen,
            source="internshala",
            ats="portal",
        )
        upsert_application(
            conn,
            apply_url="https://priors.test/jobs/portal-oa",
            status="oa",
        )
        other = _relevant_job(
            conn,
            url="https://priors.test/jobs/portal-other",
            company="OtherCo",
            score=80.0,
            first_seen_at=seen,
            source="internshala",
            ats="portal",
        )
        guest = _relevant_job(
            conn,
            url="https://priors.test/jobs/portal-guest",
            company="PortalCo",
            score=80.0,
            first_seen_at=seen,
            source="internshala",
            ats="portal",
            title="Backend Engineer Intern",
        )
        row = conn.execute(
            "SELECT company_id FROM jobs WHERE id = ?", (guest,)
        ).fetchone()
        assert row["company_id"] is None
        assert oa_job not in _guest_ids(conn)
        assert _guest_ids(conn)[:2] == [guest, other]
    finally:
        conn.close()


def test_relevant_unapplied_jobs_uses_same_prior_as_guest_rows(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    models = load_models()
    profile = _profile()
    try:
        seen = _iso(NOW)
        _plant_outcomes(conn, 19)
        _app(conn, "oa", company="PriorCo", status="oa")
        _relevant_job(
            conn,
            url="https://priors.test/jobs/other",
            company="OtherCo",
            score=80.0,
            first_seen_at=seen,
        )
        _relevant_job(
            conn,
            url="https://priors.test/jobs/prior",
            company="PriorCo",
            score=80.0,
            first_seen_at=seen,
        )
        guests = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            999,
            profile=profile,
            now=NOW,
        )
        all_jobs = relevant_unapplied_jobs(
            conn,
            models.score_model,
            models.score_prompt_version,
            profile=profile,
            now=NOW,
        )
        assert [int(r["id"]) for r in all_jobs] == [int(r["id"]) for r in guests]
        assert guests[0]["company"] == "PriorCo"
    finally:
        conn.close()


def test_guest_rows_skips_reject_even_if_relevant(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        rejected = _relevant_job(
            conn,
            url="https://example.com/jobs/reject-relevant",
            company="Acme",
            score=99.0,
            first_seen_at=_iso(NOW),
        )
        conn.execute(
            "UPDATE jobs SET hard_status = 'reject', hard_reason = 'yoe' WHERE id = ?",
            (rejected,),
        )
        conn.commit()
        ok = _relevant_job(
            conn,
            url="https://example.com/jobs/pass-relevant",
            company="Globex",
            score=80.0,
            first_seen_at=_iso(NOW),
        )
        ids = _guest_ids(conn)
        assert rejected not in ids
        assert ok in ids
    finally:
        conn.close()
