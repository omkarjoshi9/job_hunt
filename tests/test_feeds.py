from pathlib import Path

from jobdigest.feeds import parse_feed

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "feeds"
PYTHON_JOBS = FIXTURES / "python_jobs.xml"
FOSS_UNITED = FIXTURES / "foss_united.xml"


def test_parse_feed_python_jobs_two_rss_jobs():
    xml = PYTHON_JOBS.read_text(encoding="utf-8")
    jobs = parse_feed(xml, source="python-jobs")
    assert len(jobs) == 2
    assert all(j.ats == "rss" for j in jobs)
    assert jobs[0].apply_url == "https://www.python.org/jobs/1"
    assert jobs[1].apply_url == "https://www.python.org/jobs/2"
    assert "Intern" in jobs[0].title
    assert "Intern" in jobs[1].title


def test_parse_feed_foss_united_two_rss_jobs():
    xml = FOSS_UNITED.read_text(encoding="utf-8")
    jobs = parse_feed(xml, source="foss-united-rss")
    assert len(jobs) == 2
    assert all(j.ats == "rss" for j in jobs)
    assert all(j.apply_url.startswith("https://") for j in jobs)
    assert jobs[0].title == "Software Engineer Intern"
    assert jobs[1].title == "Open Source Intern"
    assert jobs[0].apply_url == "https://fossunited.org/jobs/fixture-intern-a"
    assert jobs[1].apply_url == "https://fossunited.org/jobs/fixture-intern-b"
    assert all(j.source == "foss-united-rss" for j in jobs)


def test_feeds_module_stays_httpx_feedparser():
    text = (Path(__file__).resolve().parents[1] / "src" / "jobdigest" / "feeds.py").read_text(
        encoding="utf-8"
    )
    assert "feedparser" in text
    assert "httpx" in text
    assert "scrapling" not in text
    assert "XMLFeedSpider" not in text
    assert "Fetcher.get" not in text
