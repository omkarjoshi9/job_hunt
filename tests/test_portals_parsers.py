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


def test_run_one_unknown_fetch_is_parse_error():
    from jobdigest.portals import run_one

    result = run_one(
        {
            "name": "bad",
            "url": "https://example.com/jobs",
            "parser": "generic_cards",
            "fetch": "chrome",
        },
        allowed_fn=lambda url: True,
        get_html_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )
    assert result.status == "error"
    assert result.error_class == "parse"
    assert "chrome" in (result.detail or "")


def test_run_one_unknown_parser():
    from jobdigest.portals import run_one

    result = run_one(
        {"name": "otta", "url": "https://otta.com/jobs", "parser": "kill"},
        allowed_fn=lambda url: True,
    )
    assert result.status == "error"
    assert result.error_class == "parse"


def test_run_one_dynamic_uses_injected_get_html():
    from jobdigest.portals import run_one

    seen = {}
    html = _html("generic_cards.html")

    def get_html_fn(session, url, **kwargs):
        seen["fetch"] = kwargs.get("fetch")
        seen["solve"] = kwargs.get("solve_cloudflare")
        return 200, html

    result = run_one(
        {
            "name": "spa",
            "url": "https://spa.example/jobs",
            "parser": "generic_cards",
            "fetch": "dynamic",
        },
        allowed_fn=lambda url: True,
        get_html_fn=get_html_fn,
    )
    assert seen["fetch"] == "dynamic"
    assert result.status == "ok"
    assert len(result.jobs) == 2


def test_run_one_stealth_passes_solve_cloudflare():
    from jobdigest.portals import run_one

    seen = {}
    html = _html("generic_cards.html")

    def get_html_fn(session, url, **kwargs):
        seen["fetch"] = kwargs.get("fetch")
        seen["solve"] = kwargs.get("solve_cloudflare")
        return 200, html, None

    result = run_one(
        {
            "name": "cf",
            "url": "https://cf.example/jobs",
            "parser": "generic_cards",
            "fetch": "stealth",
            "solve_cloudflare": True,
        },
        allowed_fn=lambda url: True,
        get_html_fn=get_html_fn,
    )
    assert seen["fetch"] == "stealth"
    assert seen["solve"] is True
    assert result.status == "ok"


def test_run_one_bundle_error_is_http():
    from jobdigest.portals import SessionBundle, run_one

    bundle = SessionBundle(errors={"dynamic": "no chrome"})
    result = run_one(
        {
            "name": "spa",
            "url": "https://spa.example/jobs",
            "parser": "generic_cards",
            "fetch": "dynamic",
        },
        sessions=bundle,
        allowed_fn=lambda url: True,
    )
    assert result.status == "error"
    assert result.error_class == "http"
    assert "no chrome" in (result.detail or "")
