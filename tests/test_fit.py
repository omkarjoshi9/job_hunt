from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from jobdigest.config import load_models, load_profile
from jobdigest.db import connect, upsert_job
from jobdigest.fit import fit_score, quality_score
from jobdigest.hard_filter import hard_filter
from jobdigest.legitimacy import _names_differ
from jobdigest.rank import select_diverse, sort_rows
from jobdigest.score import run as score_run

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
PROFILE = load_profile()

STRONG_JD = (
    "Responsibilities: build backend services in Python and FastAPI, ship APIs on AWS "
    "with Docker, work on LangChain RAG pipelines and security tooling. "
    "We hire freshers and new grads. " * 3
)


def row(i=1, title="Backend Software Engineer, New Grad", company="Acme", jd=STRONG_JD,
        score=90.0, ats="greenhouse", source="acme", age_days=1, loc="Pune"):
    stamp = (NOW - timedelta(days=age_days)).isoformat()
    return {
        "id": i, "title": title, "company": company, "description_text": jd,
        "score": score, "ats": ats, "source": source, "posted_at": stamp,
        "first_seen_at": stamp, "location_raw": loc,
    }


def q(**kw):
    return quality_score(row(**kw), PROFILE, NOW)


def test_same_llm_score_no_longer_means_same_rank():
    scores = {
        "great": q(),
        "intern": q(title="Software Engineer Intern"),
        "generic": q(title="Software Engineer", jd="Own the platform. " * 40),
        "stale": q(age_days=60),
        "no-skills": q(jd="Responsibilities: you will write code. freshers welcome. " * 5),
    }
    assert len(set(round(v, 1) for v in scores.values())) == len(scores)
    assert scores["great"] > scores["intern"] > scores["generic"]
    assert scores["great"] > scores["stale"] and scores["great"] > scores["no-skills"]


def test_new_grad_outranks_intern_when_otherwise_equal():
    assert q(title="Software Engineer, New Grad") > q(title="Software Engineer, Intern")


def test_core_engineering_outranks_frontend_at_a_top_company():
    backend = q(title="Backend Engineer Intern", company="Stripe")
    frontend = q(title="Frontend Engineer Intern", company="Stripe")
    assert backend > frontend


def test_top_company_and_direct_ats_boost_fit():
    base = fit_score(row(company="Nobody Inc", ats="portal"), PROFILE, NOW)
    top = fit_score(row(company="Stripe", ats="greenhouse"), PROFILE, NOW)
    assert top > base


def test_sort_rows_uses_quality_not_just_llm_score():
    good = row(1, score=70.0)
    weak = row(2, title="Software Engineer", jd="Own things. " * 60, score=95.0, age_days=40)
    ranked = sort_rows([weak, good], home="Pune", relocate=[], now=NOW, profile=PROFILE)
    assert [r["id"] for r in ranked] == [1, 2]


def test_select_diverse_applies_quality_bar_and_caps():
    p = replace(PROFILE, digest_min_quality=50.0, digest_per_company=2, digest_per_portal=2)
    rows = [row(i, company="Acme") for i in range(1, 6)]
    rows += [row(10 + i, company=f"Agg{i}", ats="portal", source="huntyourtribe") for i in range(4)]
    rows += [row(20, title="Software Engineer", jd="vague " * 50, score=10.0, age_days=90, company="Weak")]
    picked = select_diverse(rows, 12, profile=p, now=NOW)
    ids = [r["id"] for r in picked]
    assert sum(1 for r in picked if r["company"] == "Acme") == 2
    assert sum(1 for r in picked if r["source"] == "huntyourtribe") == 2
    assert 20 not in ids, "below the quality bar must not be padded in"


def test_select_diverse_returns_fewer_rather_than_padding():
    p = replace(PROFILE, digest_min_quality=99.0)
    assert select_diverse([row(1)], 12, profile=p, now=NOW) == []


@pytest.mark.parametrize(
    ("a", "b", "differ"),
    [
        ("hackerrank careers", "hackerrank", False),
        ("paytm payments services", "paytmpayments", False),
        ("stripe", "stripe inc", False),
        ("acme", "globex", True),
    ],
)
def test_company_mismatch_tolerates_careers_and_suffix_variants(a, b, differ):
    assert _names_differ(a, b) is differ


# Real titles from a production digest that should never have been sent.
JUNK = [
    "HR Intern - Talent Acquisition", "Intern:Intern:Talent Aquisition:bengaluru",
    "Content Writer Intern", "Copy Intern", "Social Media Intern at BabyMD",
    "Go-To-Market Intern", "Business Development Executive - Fresher",
    "Industrial Trainee - Finance & Accounting", "Intern - Secretarial Compliance - 598",
    "Human Resources-Intern", "Evaluation & Insights Intern, AI Delivery",
    "Software Engineer II", "Software Engineer Architect - AI Agents (Healthcare)",
    "SDE 2 Infra", "Security Engineer 2",
]
GOOD = [
    "Software Engineer, Intern", "Software Engineer, New Grad",
    "Software Development Engineer Intern", "Security Engineer Intern",
]


@pytest.mark.parametrize("title", JUNK)
def test_known_junk_titles_are_filtered(title):
    status, _ = hard_filter(
        PROFILE, title=title, location_raw="Bengaluru, India",
        description_text="Work with a great team.", company="Acme",
    )
    assert status == "reject"


@pytest.mark.parametrize("title", GOOD)
def test_known_good_titles_pass(title):
    status, reason = hard_filter(
        PROFILE, title=title, location_raw="Bengaluru, India",
        description_text="Work with a great team.", company="Stripe",
    )
    assert (status, reason) == ("pass", "ok")


def test_score_retries_jobs_the_model_omitted(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    flavours = ["Backend", "Platform", "Security", "Cloud"]
    try:
        for n, kind in enumerate(flavours):
            upsert_job(
                conn, PROFILE, apply_url=f"https://x.example/{n}", hosted_url=f"https://x.example/{n}",
                source="greenhouse", external_id=str(n), company_id=None, company=f"Co{n}",
                title=f"{kind} Software Engineer Intern", location_raw="Pune",
                description_text="Python backend intern role.", raw_json="{}", ats="greenhouse",
                employment_hint="intern",
            )
    finally:
        conn.close()
    calls: list[list[int]] = []

    def chat(messages, *, model, api_key="", json_mode=False, client=None):
        ids = [int(x) for x in re.findall(r"<<<JOB id=(\d+)>>>", messages[-1]["content"])]
        calls.append(ids)
        answered = ids[:2] if len(calls) == 1 else ids  # first call omits half
        return json.dumps({"jobs": [
            {"job_id": i, "score": 60, "relevant": True, "level_ok": True,
             "location_ok": True, "stack_ok": True, "why": "w", "kill": None}
            for i in answered
        ]})

    assert score_run(db_path=db, chat_fn=chat) == 0
    assert len(calls) == 2 and set(calls[1]) == set(calls[0]) - set(calls[0][:2])
    conn = connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM scores").fetchone()["n"] == 4
    finally:
        conn.close()
    _ = load_models


# --- repetition: the same company must not keep coming back -------------------------


def test_one_role_per_company_by_default():
    rows = [row(1, company="Stripe", title="Software Engineer, New Grad"),
            row(2, company="Stripe", title="Software Engineer, Intern"),
            row(3, company="Razorpay")]
    picked = select_diverse(rows, 12, profile=PROFILE, now=NOW)
    assert sorted(r["company"] for r in picked) == ["Razorpay", "Stripe"]


def test_company_in_cooldown_is_skipped_unless_exceptional():
    ordinary = row(1, company="Stripe", title="Software Engineer Intern", jd="Python backend. freshers " * 12)
    exceptional = row(2, company="Stripe", score=100.0)
    assert quality_score(exceptional, PROFILE, NOW) >= 85
    assert select_diverse([ordinary], 12, profile=PROFILE, now=NOW, recent_companies={"Stripe"}) == []
    got = select_diverse([exceptional], 12, profile=PROFILE, now=NOW, recent_companies={"Stripe"})
    assert [r["id"] for r in got] == [2]


def test_stale_postings_are_not_new_jobs():
    old = row(1, age_days=40)
    fresh = row(2, age_days=2)
    picked = select_diverse([old, fresh], 12, profile=PROFILE, now=NOW)
    assert [r["id"] for r in picked] == [2]


def test_digest_does_not_resend_or_repeat_across_days(tmp_path):
    """Day 1 sends Stripe; day 2 must not repeat Stripe, but a new company flows."""
    from jobdigest.db import guest_rows

    db = tmp_path / "jobs.db"
    conn = connect(db)
    models = load_models()
    p = replace(PROFILE, digest_min_quality=0.0)
    try:
        ids = {}
        for n, (company, title) in enumerate(
            [("Stripe", "Software Engineer, New Grad"), ("Stripe", "Backend Engineer Intern"), ("Razorpay", "Software Engineer, New Grad")]
        ):
            _, jid = upsert_job(
                conn, p, apply_url=f"https://x.example/{n}", hosted_url=f"https://x.example/{n}",
                source="greenhouse", external_id=str(n), company_id=None, company=company,
                title=title, location_raw="Bengaluru, India", description_text=STRONG_JD,
                raw_json="{}", ats="greenhouse", employment_hint=None,
            )
            from jobdigest.db import upsert_score
            upsert_score(conn, jid, models.score_model, models.score_prompt_version, True, 70.0,
                         {"why": "w", "stack_ok": True, "level_ok": True, "location_ok": True, "kill": None})
            ids[(company, title)] = jid
        day1 = guest_rows(conn, models.score_model, models.score_prompt_version, 12, profile=p, now=NOW)
        assert sorted(r["company"] for r in day1) == ["Razorpay", "Stripe"]  # one Stripe only
        for r in day1:
            conn.execute(
                "UPDATE jobs SET notified_at = ?, notify_kind = 'digest' WHERE id = ?",
                (NOW.isoformat(), int(r["id"])),
            )
        conn.commit()
        day2 = guest_rows(conn, models.score_model, models.score_prompt_version, 12, profile=p,
                          now=NOW + timedelta(days=1))
        assert day2 == [], "the other Stripe role must wait out the company cooldown"
        day5 = guest_rows(conn, models.score_model, models.score_prompt_version, 12, profile=p,
                          now=NOW + timedelta(days=5))
        assert [r["company"] for r in day5] == ["Stripe"]
    finally:
        conn.close()


def test_zero_to_two_years_is_not_entry_level_evidence():
    kw = dict(location_raw="Bengaluru, India", company="Acme")
    assert hard_filter(PROFILE, title="Software Engineer", description_text="0-2 years of experience", **kw)[0] == "reject"
    assert hard_filter(PROFILE, title="Software Engineer", description_text="0-1 years, freshers welcome", **kw) == ("pass", "ok")


def test_company_variants_count_as_one_company():
    rows = [row(1, company="Episode Six"), row(2, company="Episode Six US")]
    picked = select_diverse(rows, 12, profile=PROFILE, now=NOW)
    assert len(picked) == 1
    assert select_diverse([row(3, company="Episode Six US", score=30.0, jd="Python backend. freshers " * 12)], 12, profile=PROFILE, now=NOW,
                          recent_companies={"Episode Six"}) == []
    # a genuine sub-brand is a different company
    assert len(select_diverse([row(4, company="Google"), row(5, company="Google Cloud India")],
                              12, profile=PROFILE, now=NOW)) == 2
