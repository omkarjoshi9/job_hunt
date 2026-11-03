from dataclasses import replace
import json
from datetime import datetime, timedelta, timezone

from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    connect,
    guest_rows,
    upsert_company,
    upsert_job,
    upsert_score,
)
from jobdigest.digest import _guest_dicts
from jobdigest.legitimacy import (
    age_codes,
    chip,
    evaluate_structural,
    merge_flag,
)
from jobdigest.normalize import description_hash
from jobdigest.rank import blend_rank

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
HOME = "Pune"
RELOCATE = ["Bengaluru"]

_CUED = (
    "In this role you will own intern projects. Responsibilities include writing tests "
    "and shipping features with the Pune engineering team on production APIs. "
    "Day to day you will pair with mentors and review pull requests."
)

_MARKETING = (
    "Our studio ships consumer mobile products used by millions of people every month. "
    "We care about craft, design quality, and shipping polished experiences together. "
    "Competitive stipend, mentorship from senior engineers, and a modern office in India."
)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _intern(profile, **overrides):
    kwargs = dict(
        profile=profile,
        apply_url="https://example.com/jobs/legit",
        hosted_url="https://example.com/jobs/legit",
        source="greenhouse",
        external_id="legit-1",
        company_id=None,
        company="Acme",
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text=_CUED,
        raw_json="{}",
        ats="greenhouse",
        employment_hint="intern",
    )
    kwargs.update(overrides)
    return kwargs


def _score(conn, job_id: int, *, relevant: bool = True, score: float = 90.0) -> None:
    models = load_models()
    upsert_score(
        conn,
        job_id,
        models.score_model,
        models.score_prompt_version,
        relevant,
        score,
        {
            "why": "fit",
            "stack_ok": True,
            "level_ok": True,
            "location_ok": True,
            "kill": None,
        },
    )


def _guests(conn, *, now: datetime | None = None, max_age_days: int | None = None):
    models = load_models()
    profile = load_profile()
    if max_age_days is not None:
        profile = replace(profile, digest_max_age_days=max_age_days)
    return guest_rows(
        conn,
        models.score_model,
        models.score_prompt_version,
        12,
        profile=profile,
        now=now or NOW,
    )


def _codes(conn, job_id: int) -> list[str]:
    row = conn.execute(
        "SELECT legitimacy_flag, legitimacy_reasons_json FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    try:
        data = json.loads(row["legitimacy_reasons_json"] or "[]")
    except json.JSONDecodeError:
        data = []
    if not isinstance(data, list):
        data = []
    return [str(x) for x in data]


def test_age_90d_at_91_days_not_at_89():
    old = _iso(NOW - timedelta(days=91))
    young = _iso(NOW - timedelta(days=89))
    assert age_codes(None, old, now=NOW) == ["age_90d"]
    assert age_codes(None, young, now=NOW) == []
    assert age_codes(old, young, now=NOW) == ["age_90d"]
    exact = _iso(NOW - timedelta(seconds=90 * 86400))
    assert age_codes(None, exact, now=NOW) == []
    plus = _iso(NOW - timedelta(seconds=90 * 86400 + 1))
    assert age_codes(None, plus, now=NOW) == ["age_90d"]


def test_tombstoned_91d_is_not_guest(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, jid = upsert_job(conn, **_intern(profile))
        conn.execute(
            """
            UPDATE jobs
            SET first_seen_at = ?, is_active = 0, miss_count = 2,
                closed_at = ?, close_reason = ?
            WHERE id = ?
            """,
            (_iso(NOW - timedelta(days=91)), _iso(NOW), "unseen_board", jid),
        )
        conn.commit()
        _score(conn, jid)
        ids = [int(row["id"]) for row in _guests(conn)]
        assert jid not in ids
    finally:
        conn.close()


def test_active_91d_relevant_is_guest_with_chip(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, jid = upsert_job(conn, **_intern(profile))
        conn.execute(
            "UPDATE jobs SET first_seen_at = ? WHERE id = ?",
            (_iso(NOW - timedelta(days=91)), jid),
        )
        conn.commit()
        _score(conn, jid)
        assert jid not in [int(r["id"]) for r in _guests(conn)], "default window drops stale"
        rows = _guests(conn, max_age_days=365)
        ids = [int(row["id"]) for row in rows]
        assert jid in ids
        guest = _guest_dicts(rows, now=NOW)
        match = next(item for item in guest if item["id"] == jid)
        assert match["legitimacy_flag"] == "suspect"
        assert match["legitimacy_chip"] == "suspect:90d"
    finally:
        conn.close()


def test_company_mismatch_agency_name(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        cid = upsert_company(conn, "razorpay", "Razorpay", "greenhouse", "razorpay", True)
        _, jid = upsert_job(
            conn,
            **_intern(
                profile,
                company_id=cid,
                company="HireFast Staffing",
                apply_url="https://example.com/jobs/agency",
                external_id="agency-1",
            ),
        )
        assert "company_mismatch" in _codes(conn, jid)
        flag = conn.execute(
            "SELECT legitimacy_flag FROM jobs WHERE id = ?", (jid,)
        ).fetchone()["legitimacy_flag"]
        assert flag == "suspect"
    finally:
        conn.close()
    flag, codes = evaluate_structural(
        {
            "company_id": 1,
            "raw_company": "HireFast Staffing",
            "company": "HireFast Staffing",
            "description_text": _CUED,
            "description_hash": description_hash(_CUED),
            "source": "greenhouse",
            "external_id": "1",
            "apply_url_norm": "https://example.com/a",
            "canonical_job_id": None,
            "hard_status": "pass",
        },
        company_display_name="Razorpay",
    )
    assert flag == "suspect"
    assert "company_mismatch" in codes


def test_company_mismatch_skips_normalized_legal_suffix():
    flag, codes = evaluate_structural(
        {
            "company_id": 1,
            "raw_company": "Razorpay Pvt. Ltd.",
            "company": "Razorpay",
            "description_text": _CUED,
            "description_hash": description_hash(_CUED),
            "source": "greenhouse",
            "external_id": "1",
            "apply_url_norm": "https://example.com/a",
            "canonical_job_id": None,
            "hard_status": "pass",
        },
        company_display_name="Razorpay",
    )
    assert "company_mismatch" not in codes
    assert flag == "ok"


def test_company_mismatch_skips_portals_without_company_id(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, jid = upsert_job(
            conn,
            **_intern(
                profile,
                company_id=None,
                company="HireFast Staffing",
                source="hasjob",
                ats="portal",
                apply_url="https://hasjob.example/agency",
                external_id="p-agency",
            ),
        )
        assert "company_mismatch" not in _codes(conn, jid)
    finally:
        conn.close()
    flag, codes = evaluate_structural(
        {
            "company_id": None,
            "raw_company": "HireFast Staffing",
            "company": "HireFast Staffing",
            "description_text": _CUED,
            "hard_status": "pass",
        },
        company_display_name="Razorpay",
    )
    assert "company_mismatch" not in codes
    assert flag == "ok"


def test_no_responsibilities_fires_on_marketing_blurb():
    assert len(_MARKETING.replace("\n", " ").strip()) >= 200
    flag, codes = evaluate_structural(
        {
            "company_id": None,
            "description_text": _MARKETING,
            "hard_status": "pass",
        }
    )
    assert flag == "suspect"
    assert "no_responsibilities" in codes


def test_no_responsibilities_skips_short_and_empty_sr():
    empty_flag, empty_codes = evaluate_structural(
        {"company_id": None, "description_text": "", "hard_status": "pass"}
    )
    short_flag, short_codes = evaluate_structural(
        {
            "company_id": None,
            "description_text": "Intern role in Pune",
            "hard_status": "pass",
        }
    )
    almost = "a" * 199
    almost_flag, almost_codes = evaluate_structural(
        {"company_id": None, "description_text": almost, "hard_status": "pass"}
    )
    assert "no_responsibilities" not in empty_codes
    assert "no_responsibilities" not in short_codes
    assert "no_responsibilities" not in almost_codes
    assert empty_flag == short_flag == almost_flag == "ok"


def test_us_lang_in_first_2000_is_hard_reject_not_guest(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, jid = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://example.com/jobs/us-early",
                external_id="us-early",
                description_text=(
                    "Must have work authorization in the United States. " + _CUED
                ),
            ),
        )
        row = conn.execute(
            "SELECT hard_status, hard_reason FROM jobs WHERE id = ?", (jid,)
        ).fetchone()
        assert row["hard_status"] == "reject"
        assert row["hard_reason"] == "remote_us"
        _score(conn, jid)
        ids = [int(r["id"]) for r in _guests(conn)]
        assert jid not in ids
    finally:
        conn.close()


def test_us_lang_after_2000_pass_gets_us_chip(tmp_path):
    pad = (
        "In this role you will intern in Pune. Responsibilities include tests. " * 40
    )[:2000]
    assert "united states" not in pad.casefold()
    desc = pad + " Candidates must have work authorization in the United States."
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        cid = upsert_company(conn, "acme", "Acme", "greenhouse", "acme", True)
        _, jid = upsert_job(
            conn,
            **_intern(
                profile,
                company_id=cid,
                company="Acme",
                apply_url="https://example.com/jobs/us-late",
                external_id="us-late",
                description_text=desc,
            ),
        )
        row = conn.execute(
            "SELECT hard_status, legitimacy_flag, legitimacy_reasons_json FROM jobs WHERE id = ?",
            (jid,),
        ).fetchone()
        assert row["hard_status"] == "pass"
        assert row["legitimacy_flag"] == "suspect"
        assert "us_lang_india_co" in json.loads(row["legitimacy_reasons_json"])
        _score(conn, jid)
        guests = _guest_dicts(_guests(conn), now=NOW)
        match = next(item for item in guests if item["id"] == jid)
        assert match["legitimacy_chip"] == "suspect:us"
    finally:
        conn.close()


def test_jd_repost_same_hash_new_external_id(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, first = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://example.com/jobs/repost-a",
                external_id="repost-a",
                location_raw="Pune",
            ),
        )
        _, second = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://example.com/jobs/repost-b",
                external_id="repost-b",
                location_raw="Bengaluru",
            ),
        )
        assert first != second
        assert "jd_repost" in _codes(conn, second)
        canon = conn.execute(
            "SELECT canonical_job_id FROM jobs WHERE id IN (?, ?)",
            (first, second),
        ).fetchall()
        assert all(row["canonical_job_id"] is None for row in canon)
    finally:
        conn.close()


def test_jd_repost_ignores_empty_hash(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, a = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://example.com/jobs/empty-a",
                external_id="empty-a",
                location_raw="Pune",
                description_text="",
            ),
        )
        _, b = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://example.com/jobs/empty-b",
                external_id="empty-b",
                location_raw="Bengaluru",
                description_text="   ",
            ),
        )
        assert "jd_repost" not in _codes(conn, a)
        assert "jd_repost" not in _codes(conn, b)
        assert description_hash("") == description_hash("   ")
    finally:
        conn.close()


def test_dual_hash_alias_is_not_jd_repost(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    try:
        _, portal_id = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://hasjob.example/alias",
                source="hasjob",
                external_id="alias-p",
                ats="portal",
                company="Razorpay Software Private Limited",
                title="Software Engineer Intern (Bengaluru)",
                location_raw="Bengaluru",
            ),
        )
        _, ats_id = upsert_job(
            conn,
            **_intern(
                profile,
                apply_url="https://boards.greenhouse.io/razorpay/jobs/alias",
                source="razorpay",
                external_id="alias-g",
                ats="greenhouse",
                company="Razorpay",
                title="Software Engineer Intern",
                location_raw="Bengaluru",
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
        assert "jd_repost" not in _codes(conn, portal_id)
        assert "jd_repost" not in _codes(conn, ats_id)
    finally:
        conn.close()


def test_evaluate_does_not_change_score_or_relevant(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    models = load_models()
    try:
        _, jid = upsert_job(conn, **_intern(profile))
        _score(conn, jid, score=91.0)
        before = conn.execute(
            """
            SELECT score, relevant, reasons_json, stale, model, prompt_version
            FROM scores WHERE job_id = ?
            """,
            (jid,),
        ).fetchone()
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        evaluate_structural(
            {
                "company_id": job["company_id"],
                "raw_company": job["raw_company"],
                "company": job["company"],
                "description_text": job["description_text"],
                "description_hash": job["description_hash"],
                "source": job["source"],
                "external_id": job["external_id"],
                "apply_url_norm": job["apply_url_norm"],
                "canonical_job_id": job["canonical_job_id"],
                "hard_status": job["hard_status"],
            }
        )
        upsert_job(conn, **_intern(profile))
        after = conn.execute(
            """
            SELECT score, relevant, reasons_json, stale, model, prompt_version
            FROM scores WHERE job_id = ?
            """,
            (jid,),
        ).fetchone()
        assert after["score"] == before["score"] == 91.0
        assert int(after["relevant"]) == int(before["relevant"]) == 1
        assert after["reasons_json"] == before["reasons_json"]
        assert int(after["stale"]) == int(before["stale"]) == 0
        assert after["model"] == before["model"] == models.score_model
        assert after["prompt_version"] == before["prompt_version"]
        assert after["prompt_version"] == models.score_prompt_version
    finally:
        conn.close()


def test_blend_rank_identical_with_suspect_flag():
    row_ok = {
        "id": 1,
        "score": 90.0,
        "first_seen_at": _iso(NOW - timedelta(hours=4)),
        "location_raw": "Pune",
        "title": "Software Engineer Intern",
        "legitimacy_flag": "ok",
    }
    row_suspect = dict(
        row_ok,
        legitimacy_flag="suspect",
        legitimacy_reasons_json='["age_90d"]',
    )
    kwargs = dict(home=HOME, relocate=RELOCATE, now=NOW)
    assert blend_rank(row_ok, **kwargs) == blend_rank(row_suspect, **kwargs)


def test_chip_empty_when_ok():
    assert chip("ok", []) == ""
    assert chip("ok", ["age_90d"]) == ""
    assert chip(None, []) == ""


def test_chip_max_24_chars():
    assert chip("suspect", ["age_90d"]) == "suspect:90d"
    assert chip("suspect", ["company_mismatch"]) == "suspect:mismatch"
    assert chip("suspect", ["no_responsibilities"]) == "suspect:thin"
    assert chip("suspect", ["us_lang_india_co"]) == "suspect:us"
    assert chip("suspect", ["jd_repost"]) == "suspect:repost"
    assert chip("suspect", ["age_90d", "jd_repost"]) == "suspect"
    assert len(chip("suspect", ["age_90d"])) <= 24
    assert len(chip("suspect", ["x" * 80])) <= 24
    assert len(chip("suspect", ["age_90d", "company_mismatch", "jd_repost"])) <= 24
    flag, codes = merge_flag(["company_mismatch"], ["age_90d"])
    assert flag == "suspect"
    assert codes == ["company_mismatch", "age_90d"]
    assert len(chip(flag, codes)) <= 24
