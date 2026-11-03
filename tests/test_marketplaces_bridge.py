import pytest

from jobdigest.marketplaces import run_one
from jobdigest.marketplaces.jobspy_bridge import row_to_normalized, scrape_row


def test_row_to_normalized_prefers_direct_url_and_marketplace_ats():
    job = row_to_normalized(
        {
            "id": "nk-1",
            "title": "Software Engineer Intern",
            "company": "Acme",
            "location": "Bengaluru, Karnataka, India",
            "job_url": "https://www.naukri.com/job/1",
            "job_url_direct": "https://boards.greenhouse.io/acme/jobs/1",
            "job_type": "internship, fulltime",
            "is_remote": False,
            "work_from_home_type": "Hybrid",
            "description": "<p>Intern role in Bengaluru</p>",
            "date_posted": "2026-09-01",
        },
        source="naukri-intern-india",
    )
    assert job is not None
    assert job.apply_url == "https://boards.greenhouse.io/acme/jobs/1"
    assert job.hosted_url == "https://www.naukri.com/job/1"
    assert job.source == "naukri-intern-india"
    assert job.ats == "marketplace"
    assert job.external_id == "nk-1"
    assert job.employment_hint == "internship"
    assert job.workplace_hint == "Hybrid"
    assert "Intern role" in job.description_text
    assert job.posted_at == "2026-09-01T00:00:00Z"


def test_row_to_normalized_skips_untitled_and_identityless():
    assert (
        row_to_normalized(
            {"title": "", "job_url": "https://example.com/x", "id": "1"},
            source="naukri",
        )
        is None
    )
    assert row_to_normalized({"title": "Intern"}, source="naukri") is None


def test_row_to_normalized_remote_hint_and_url_hash():
    job = row_to_normalized(
        {
            "title": "Intern",
            "job_url": "https://www.indeed.com/viewjob?jk=abc",
            "is_remote": True,
        },
        source="indeed-intern-india",
    )
    assert job is not None
    assert job.apply_url.startswith("https://")
    assert job.external_id
    assert job.workplace_hint == "remote"


def test_scrape_row_maps_list_payload_and_skips_bad_rows():
    captured = {}

    def scrape_fn(**kwargs):
        captured.update(kwargs)
        return [
            {
                "id": "in-1",
                "title": "Software Engineer Intern",
                "company": "Acme",
                "location": "Pune",
                "job_url": "https://in.indeed.com/viewjob?jk=1",
                "description": "Intern in Pune",
            },
            {"title": "", "job_url": "https://in.indeed.com/viewjob?jk=2"},
        ]

    jobs = scrape_row(
        {
            "name": "indeed-intern-india",
            "site": "indeed",
            "search_term": "software intern",
            "country_indeed": "India",
            "results_wanted": 20,
        },
        scrape_fn=scrape_fn,
    )
    assert captured["site_name"] == ["indeed"]
    assert captured["country_indeed"] == "India"
    assert captured["verbose"] == 0
    assert len(jobs) == 1
    assert jobs[0].source == "indeed-intern-india"
    assert jobs[0].ats == "marketplace"


def test_naukri_defaults_linkedin_fetch_description():
    captured = {}

    def scrape_fn(**kwargs):
        captured.update(kwargs)
        return []

    scrape_row({"name": "naukri-intern-india", "site": "naukri"}, scrape_fn=scrape_fn)
    assert captured["linkedin_fetch_description"] is True
    assert captured["site_name"] == ["naukri"]


def test_run_one_unknown_site_is_parse_error():
    result = run_one({"name": "bad", "site": "internshala"})
    assert result.status == "error"
    assert result.error_class == "parse"
    assert "internshala" in (result.detail or "")


def test_run_one_import_error_is_config():
    def scrape_fn(**kwargs):
        raise ImportError("No module named 'jobspy'")

    result = run_one({"name": "naukri-intern-india", "site": "naukri"}, scrape_fn=scrape_fn)
    assert result.status == "error"
    assert result.error_class == "config"
    assert result.detail == "python-jobspy not installed"


def test_run_one_empty_scrape_is_ok():
    result = run_one(
        {"name": "naukri-intern-india", "site": "naukri"},
        scrape_fn=lambda **kwargs: [],
    )
    assert result.status == "ok"
    assert result.jobs == []


def test_run_one_forwards_proxies(monkeypatch):
    captured = {}

    def scrape_fn(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setenv("JOBDIGEST_MARKETPLACE_PROXIES", "host:1, user:pass@host:2")
    result = run_one({"name": "linkedin-intern-india", "site": "linkedin"}, scrape_fn=scrape_fn)
    assert result.status == "ok"
    assert captured["proxies"] == ["host:1", "user:pass@host:2"]


def test_posted_at_normalized_for_all_input_types():
    import datetime as dt

    pd = pytest.importorskip("pandas")

    from jobdigest.marketplaces.jobspy_bridge import _posted_at

    assert _posted_at(dt.date(2026, 9, 1)) == "2026-09-01T00:00:00Z"
    assert _posted_at(dt.datetime(2026, 9, 1, 12, 0, 5, 7)) == "2026-09-01T12:00:05Z"
    assert _posted_at(pd.Timestamp("2026-09-01 12:00:00")) == "2026-09-01T12:00:00Z"
    assert _posted_at(pd.Timestamp("2026-09-01 12:00:00", tz="Asia/Kolkata")) == "2026-09-01T06:30:00Z"
    assert _posted_at(pd.NaT) is None
    assert _posted_at(float("nan")) is None
    assert _posted_at(None) is None
    assert _posted_at("") is None
    assert _posted_at("2026-09-01") == "2026-09-01T00:00:00Z"
