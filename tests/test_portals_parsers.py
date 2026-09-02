from pathlib import Path

from jobdigest.portals import foss_united, generic, hasjob, null_community
from jobdigest.portals.session import is_challenge

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "portals"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_foss_united_fixture_two_jobs():
    jobs = foss_united.parse(
        _html("foss_united.html"), source="foss-united", base_url="https://fossunited.org/jobs"
    )
    assert len(jobs) == 2
    assert jobs[0].title == "Software Engineer Intern"
    assert jobs[1].title == "Backend Intern"
    assert jobs[0].apply_url == "https://fossunited.org/jobs/1"
    assert jobs[1].apply_url == "https://fossunited.org/jobs/2"
    assert jobs[0].location_raw == "Bengaluru"
    assert jobs[1].location_raw == "Pune"
    assert all(j.apply_url.startswith("http") for j in jobs)


def test_null_community_fixture_two_jobs():
    jobs = null_community.parse(
        _html("null_community.html"),
        source="null-jobs",
        base_url="https://jobs.null.community/",
    )
    assert len(jobs) == 2
    assert jobs[0].title == "Platform Intern"
    assert jobs[1].title == "ML Intern"
    assert all(j.apply_url.startswith("https://") for j in jobs)


def test_hasjob_fixture_two_jobs():
    jobs = hasjob.parse(
        _html("hasjob.html"), source="hasjob", base_url="https://hasjob.co/"
    )
    assert len(jobs) == 2
    assert "Intern" in jobs[0].title
    assert "Intern" in jobs[1].title
    assert jobs[0].company == "Hasjob Co"
    assert all(j.apply_url.startswith("http") for j in jobs)


def test_generic_cards_fixture_two_jobs():
    jobs = generic.parse(
        _html("generic_cards.html"), source="generic", base_url="https://example.com/"
    )
    assert len(jobs) == 2
    assert jobs[0].title == "Frontend Intern"
    assert jobs[1].title == "Backend Intern"
    assert jobs[0].apply_url == "https://example.com/a"
    assert jobs[1].apply_url.startswith("http")


def test_challenge_html_is_challenge():
    html = _html("challenge.html")
    assert is_challenge(html, 200) is True
    assert is_challenge("ok", 403) is True
