from datetime import datetime, timedelta, timezone

import pytest

from jobdigest.normalize import normalize_company
from jobdigest.rank import (
    LOC_HOME,
    LOC_OTHER,
    LOC_RELOCATE,
    PRIOR_POSITIVE,
    RECENCY_MAX,
    age_chip,
    blend_rank,
    location_pref,
    recency_boost,
    sort_rows,
    source_chip,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
HOME = "Pune"
RELOCATE = ["Bengaluru"]


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row(
    *,
    id: int,
    score: float,
    first_seen_at: str | None,
    location_raw: str,
    title: str = "Software Engineer Intern",
    company: str = "",
) -> dict:
    return {
        "id": id,
        "score": score,
        "first_seen_at": first_seen_at,
        "location_raw": location_raw,
        "title": title,
        "company": company,
    }


def test_recency_boost_age_zero_is_max():
    assert recency_boost(iso(NOW), NOW) == RECENCY_MAX


def test_recency_boost_four_hours():
    seen = iso(NOW - timedelta(hours=4))
    assert recency_boost(seen, NOW) == pytest.approx(41 / 3)


def test_recency_boost_six_days():
    seen = iso(NOW - timedelta(days=6))
    assert recency_boost(seen, NOW) == 2.0


def test_recency_boost_seven_days_is_zero():
    seen = iso(NOW - timedelta(days=7))
    assert recency_boost(seen, NOW) == 0.0


def test_recency_boost_older_than_window_is_zero():
    seen = iso(NOW - timedelta(days=10))
    assert recency_boost(seen, NOW) == 0.0


def test_recency_boost_missing_or_blank_is_zero():
    assert recency_boost(None, NOW) == 0.0
    assert recency_boost("", NOW) == 0.0
    assert recency_boost("  ", NOW) == 0.0


def test_recency_boost_unparseable_is_zero():
    assert recency_boost("not-a-date", NOW) == 0.0
    assert recency_boost("yesterday", NOW) == 0.0


def test_recency_boost_naive_iso_treated_as_utc():
    assert recency_boost("2026-09-09T12:00:00", NOW) == RECENCY_MAX


def test_recency_boost_future_clamped_to_max():
    future = iso(NOW + timedelta(hours=1))
    boost = recency_boost(future, NOW)
    assert boost == RECENCY_MAX
    assert boost <= RECENCY_MAX


def test_location_pref_pune_home():
    assert (
        location_pref("Pune, Maharashtra", "Intern", home=HOME, relocate=RELOCATE)
        == LOC_HOME
    )


def test_location_pref_bengaluru_relocate():
    assert location_pref("Bengaluru", "Intern", home=HOME, relocate=RELOCATE) == LOC_RELOCATE


def test_location_pref_bangalore_equals_bengaluru():
    assert (
        location_pref("Bangalore, India", "Intern", home=HOME, relocate=RELOCATE)
        == LOC_RELOCATE
    )
    assert location_pref("Bengalooru", "Intern", home=HOME, relocate=RELOCATE) == LOC_RELOCATE


def test_location_pref_bangalore_in_title_empty_location():
    assert (
        location_pref("", "SWE Intern - Bangalore", home=HOME, relocate=RELOCATE)
        == LOC_RELOCATE
    )


def test_location_pref_other_india_zero():
    assert location_pref("Mumbai", "Intern", home=HOME, relocate=RELOCATE) == LOC_OTHER
    assert location_pref("Hyderabad", "Intern", home=HOME, relocate=RELOCATE) == LOC_OTHER


def test_location_pref_home_wins_over_relocate_on_same_string():
    assert (
        location_pref("Pune / Bengaluru", "Intern", home=HOME, relocate=RELOCATE)
        == LOC_HOME
    )


def test_location_pref_does_not_read_description():
    # location_pref has no description_text argument; JD body cannot boost rank.
    import inspect

    params = inspect.signature(location_pref).parameters
    assert "description_text" not in params
    assert "description" not in params


def test_blend_rank_example_a_fresh_pune_beats_stale_mumbai():
    job_a = _row(
        id=1,
        score=78,
        first_seen_at=iso(NOW - timedelta(hours=4)),
        location_raw="Pune",
    )
    job_b = _row(
        id=2,
        score=81,
        first_seen_at=iso(NOW - timedelta(days=6)),
        location_raw="Mumbai",
    )
    rank_a = blend_rank(job_a, home=HOME, relocate=RELOCATE, now=NOW)
    rank_b = blend_rank(job_b, home=HOME, relocate=RELOCATE, now=NOW)
    assert rank_a == pytest.approx(78 + 41 / 3 + 8)
    assert rank_b == pytest.approx(83)
    assert rank_a > rank_b


def test_blend_rank_example_b_pune_beats_equal_age_bengaluru():
    seen = iso(NOW)
    pune = _row(id=1, score=80, first_seen_at=seen, location_raw="Pune")
    blr = _row(id=2, score=80, first_seen_at=seen, location_raw="Bengaluru")
    rank_pune = blend_rank(pune, home=HOME, relocate=RELOCATE, now=NOW)
    rank_blr = blend_rank(blr, home=HOME, relocate=RELOCATE, now=NOW)
    assert rank_pune == pytest.approx(102)
    assert rank_blr == pytest.approx(98)
    assert rank_pune > rank_blr


def test_blend_rank_example_c_new_hyderabad_beats_six_day_pune():
    hyd = _row(id=1, score=80, first_seen_at=iso(NOW), location_raw="Hyderabad")
    pune = _row(
        id=2,
        score=80,
        first_seen_at=iso(NOW - timedelta(days=6)),
        location_raw="Pune",
    )
    rank_hyd = blend_rank(hyd, home=HOME, relocate=RELOCATE, now=NOW)
    rank_pune = blend_rank(pune, home=HOME, relocate=RELOCATE, now=NOW)
    assert rank_hyd == pytest.approx(94)
    assert rank_pune == pytest.approx(90)
    assert rank_hyd > rank_pune


def test_sort_rows_tie_breaks_on_id_asc():
    seen = iso(NOW)
    older_id = _row(id=5, score=80, first_seen_at=seen, location_raw="Mumbai")
    newer_id = _row(id=2, score=80, first_seen_at=seen, location_raw="Mumbai")
    ordered = sort_rows(
        [older_id, newer_id], home=HOME, relocate=RELOCATE, now=NOW
    )
    assert [int(r["id"]) for r in ordered] == [2, 5]


def test_age_chip_four_hours():
    seen = iso(NOW - timedelta(hours=4))
    assert age_chip(seen, NOW) == "4h"


def test_age_chip_two_days():
    seen = iso(NOW - timedelta(days=2))
    assert age_chip(seen, NOW) == "2d"


def test_age_chip_seven_days_not_empty_when_boost_zero():
    seen = iso(NOW - timedelta(days=7))
    assert age_chip(seen, NOW) == "7d"
    assert recency_boost(seen, NOW) == 0.0


def test_age_chip_under_one_hour():
    seen = iso(NOW - timedelta(minutes=30))
    assert age_chip(seen, NOW) == "<1h"


def test_age_chip_blank_or_unparseable():
    assert age_chip(None, NOW) == ""
    assert age_chip("", NOW) == ""
    assert age_chip("yesterday", NOW) == ""


def test_age_chip_four_hours_shares_parser_with_recency_boost():
    seen = iso(NOW - timedelta(hours=4))
    assert age_chip(seen, NOW) == "4h"
    assert recency_boost(seen, NOW) == pytest.approx(41 / 3)


def test_age_chip_future_is_under_one_hour():
    seen = iso(NOW + timedelta(hours=1))
    assert age_chip(seen, NOW) == "<1h"


def test_source_chip_ats_portal_rss():
    assert source_chip("greenhouse") == "ats"
    assert source_chip("lever") == "ats"
    assert source_chip("Workable") == "ats"
    assert source_chip("portal") == "portal"
    assert source_chip("PORTAL") == "portal"
    assert source_chip("rss") == "rss"
    assert source_chip("marketplace") == "marketplace"
    assert source_chip("MARKETPLACE") == "marketplace"
    assert source_chip("") == "ats"
    assert source_chip(None) == "ats"


def test_llm_lead_beats_max_boost_including_prior():
    stale = iso(NOW - timedelta(days=10))
    fresh = iso(NOW)
    high = _row(id=1, score=100, first_seen_at=stale, location_raw="Mumbai")
    pune = _row(
        id=2, score=72, first_seen_at=fresh, location_raw="Pune", company="Acme"
    )
    priors = {normalize_company("Acme").casefold(): PRIOR_POSITIVE}
    rank_high = blend_rank(high, home=HOME, relocate=RELOCATE, now=NOW, priors=priors)
    rank_pune = blend_rank(pune, home=HOME, relocate=RELOCATE, now=NOW, priors=priors)
    assert rank_pune == pytest.approx(72 + 14 + 8 + 5)
    assert rank_pune == pytest.approx(99)
    assert rank_high == pytest.approx(100)
    assert rank_high > rank_pune


def test_prior_can_flip_5_point_llm_gap():
    stale = iso(NOW - timedelta(days=10))
    fresh = iso(NOW)
    mumbai = _row(id=1, score=100, first_seen_at=stale, location_raw="Mumbai")
    pune = _row(
        id=2, score=77, first_seen_at=fresh, location_raw="Pune", company="Acme"
    )
    priors = {normalize_company("Acme").casefold(): PRIOR_POSITIVE}
    rank_mumbai = blend_rank(
        mumbai, home=HOME, relocate=RELOCATE, now=NOW, priors=priors
    )
    rank_pune = blend_rank(pune, home=HOME, relocate=RELOCATE, now=NOW, priors=priors)
    assert rank_pune == pytest.approx(77 + 14 + 8 + 5)
    assert rank_pune == pytest.approx(104)
    assert rank_mumbai == pytest.approx(100)
    assert rank_pune > rank_mumbai


def test_location_match_is_word_boundary():
    assert location_pref("Punekar Tech Park", "Intern", home="Pune", relocate=[]) == LOC_OTHER
    assert (
        location_pref("Hybrid - Pune.", "Intern", home="Pune", relocate=[]) == LOC_HOME
    )
