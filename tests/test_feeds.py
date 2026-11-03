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


def test_parse_feed_populates_posted_at_iso_z():
    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        "<item><title>Intern</title><link>https://e.com/1</link>"
        "<pubDate>Mon, 01 Sep 2025 10:30:00 +0530</pubDate></item>"
        "<item><title>No date</title><link>https://e.com/2</link></item>"
        "</channel></rss>"
    )
    jobs = parse_feed(xml, source="s")
    assert jobs[0].posted_at == "2025-09-01T05:00:00Z"
    assert jobs[1].posted_at is None


def test_run_one_timeout_classified_by_type():
    import httpx

    from jobdigest.feeds import run_one

    def boom(url):
        raise httpx.ReadTimeout("slow")

    res = run_one({"name": "f", "url": "https://e.com/rss"}, http_get=boom)
    assert res.status == "error" and res.error_class == "timeout"

    def other(url):
        raise RuntimeError("a timeout word in message")

    res = run_one({"name": "f", "url": "https://e.com/rss"}, http_get=other)
    assert res.error_class == "http"


def test_run_one_empty_feed_is_parse_error():
    from jobdigest.feeds import run_one

    xml = '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'
    res = run_one({"name": "f", "url": "https://e.com/rss"}, http_get=lambda u: xml)
    assert res.status == "error" and res.error_class == "parse"


def test_run_one_ok():
    from jobdigest.feeds import run_one

    xml = PYTHON_JOBS.read_text(encoding="utf-8")
    res = run_one({"name": "f", "url": "https://e.com/rss"}, http_get=lambda u: xml)
    assert res.status == "ok" and len(res.jobs) == 2
