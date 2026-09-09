import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from jobdigest.ats.base import FetchResult, FetchStatus, NormalizedJob
from jobdigest.cli import main
from jobdigest.collect import run
from jobdigest.db import connect
from jobdigest.portals import PortalResult

ROOT = Path(__file__).resolve().parents[1]
DUP_URL = "https://dup.example/x"


def _intern_job(*, source: str, ats: str, url: str = DUP_URL) -> NormalizedJob:
    return NormalizedJob(
        title="Software Engineer Intern",
        company="Example Corp",
        location_raw="Pune",
        apply_url=url,
        hosted_url=url,
        employment_hint="intern",
        workplace_hint=None,
        description_text="Intern role in Pune",
        source=source,
        ats=ats,
        external_id="x",
        raw_json="{}",
        fetched_at=datetime.now(timezone.utc),
    )


def _enabled_companies():
    return {
        "companies": [
            {
                "id": "example",
                "name": "Example Corp",
                "ats": "greenhouse",
                "token": "example",
                "enabled": True,
            }
        ]
    }


def _enabled_portal():
    return {
        "portals": [
            {
                "name": "dup-portal",
                "url": "https://dup.example/board",
                "enabled": True,
                "parser": "generic_cards",
            }
        ]
    }


def _enabled_feed():
    return {
        "feeds": [
            {
                "name": "dup-feed",
                "url": "https://dup.example/rss",
                "enabled": True,
            }
        ]
    }


def test_portal_same_url_does_not_double(monkeypatch, tmp_path):
    ats_job = _intern_job(source="example", ats="greenhouse")
    portal_job = _intern_job(source="dup-portal", ats="portal")

    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, [ats_job])

    def portal_run_fn(row):
        _ = row
        return PortalResult(status="ok", jobs=[portal_job], http_status=200)

    def boom_feed(row):
        raise AssertionError("feed should stay disabled")

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _enabled_portal)
    monkeypatch.setattr(
        "jobdigest.collect.load_feeds", lambda: {"feeds": [{"name": "off", "url": "https://x", "enabled": False}]}
    )
    db = tmp_path / "jobs.db"
    code = run(
        db_path=db,
        sleep=lambda _: None,
        fetch_fn=fetch_fn,
        portal_run_fn=portal_run_fn,
        feed_run_fn=boom_feed,
    )
    assert code == 0
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        assert count == 1
    finally:
        conn.close()


def test_portal_and_feed_same_url_does_not_double(monkeypatch, tmp_path):
    portal_job = _intern_job(source="dup-portal", ats="portal")
    feed_job = _intern_job(source="dup-feed", ats="rss")

    def boom_ats(company, client, sleep):
        raise AssertionError("ATS should stay skipped")

    def portal_run_fn(row):
        _ = row
        return PortalResult(status="ok", jobs=[portal_job], http_status=200)

    def feed_run_fn(row):
        _ = row
        return PortalResult(status="ok", jobs=[feed_job], http_status=200)

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _enabled_portal)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _enabled_feed)
    db = tmp_path / "jobs.db"
    code = run(
        db_path=db,
        sleep=lambda _: None,
        fetch_fn=boom_ats,
        skip_ats=True,
        portal_run_fn=portal_run_fn,
        feed_run_fn=feed_run_fn,
    )
    assert code == 0
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        run_row = conn.execute(
            "SELECT stats_json FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        stats = json.loads(run_row["stats_json"])
        assert count == 1
        assert stats["fetched"] == 2
        assert stats["inserted"] == 1
        assert stats["updated"] == 1
    finally:
        conn.close()


def test_ats_only_skips_portals_and_feeds(monkeypatch, tmp_path):
    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, [])

    def boom_portal(row):
        raise AssertionError("portals must be skipped")

    def boom_feed(row):
        raise AssertionError("feeds must be skipped")

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _enabled_portal)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _enabled_feed)
    code = run(
        db_path=tmp_path / "jobs.db",
        sleep=lambda _: None,
        fetch_fn=fetch_fn,
        ats_only=True,
        portal_run_fn=boom_portal,
        feed_run_fn=boom_feed,
    )
    assert code == 0


def test_collect_default_yaml_no_enabled_portals(monkeypatch, tmp_path):
    payload = yaml.safe_load(
        (ROOT / "config" / "companies.example.yaml").read_text(encoding="utf-8")
    )
    portals = yaml.safe_load(
        (ROOT / "config" / "portals.example.yaml").read_text(encoding="utf-8")
    )
    feeds = yaml.safe_load(
        (ROOT / "config" / "feeds.example.yaml").read_text(encoding="utf-8")
    )
    monkeypatch.setattr("jobdigest.collect.load_companies", lambda: payload)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: portals)
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: feeds)
    db = tmp_path / "jobs.db"
    code = run(db_path=db, sleep=lambda _: None)
    assert code == 0
    conn = connect(db)
    try:
        jobs = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        assert jobs == 0
    finally:
        conn.close()


def test_cli_collect_ats_only(monkeypatch, tmp_path):
    monkeypatch.setattr("jobdigest.collect.load_companies", lambda: {"companies": []})
    db = tmp_path / "jobs.db"
    assert main(["collect", "--ats-only", "--db", str(db)]) == 0


def test_cli_collect_skip_flags(monkeypatch, tmp_path):
    monkeypatch.setattr("jobdigest.collect.load_companies", lambda: {"companies": []})
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    assert (
        main(
            [
                "collect",
                "--skip-ats",
                "--skip-feeds",
                "--portal-fetch",
                "http",
                "--db",
                str(db),
            ]
        )
        == 0
    )


def test_git_yaml_portals_and_feeds_disabled():
    portals = yaml.safe_load(
        (ROOT / "config" / "portals.example.yaml").read_text(encoding="utf-8")
    )
    feeds = yaml.safe_load(
        (ROOT / "config" / "feeds.example.yaml").read_text(encoding="utf-8")
    )
    marketplaces = yaml.safe_load(
        (ROOT / "config" / "marketplaces.example.yaml").read_text(encoding="utf-8")
    )
    assert all(not row.get("enabled") for row in portals["portals"])
    assert all(not row.get("enabled") for row in feeds["feeds"])
    assert all(not row.get("enabled") for row in marketplaces["marketplaces"])
    hunt = next(row for row in portals["portals"] if row["name"] == "huntyourtribe-intern")
    assert hunt["fetch"] == "dynamic"
    assert hunt["parser"] == "generic_cards"
    from jobdigest.portals.session import ALLOWED_FETCH

    for row in portals["portals"]:
        raw = row.get("fetch")
        if raw:
            assert str(raw) in ALLOWED_FETCH
        if row.get("parser") == "kill":
            assert row["enabled"] is False


def test_portal_fetch_http_skips_dynamic_rows(monkeypatch, tmp_path):
    seen = []

    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, [])

    def portal_run_fn(row):
        seen.append(row["name"])
        return PortalResult(status="ok", jobs=[], http_status=200)

    def boom_feed(row):
        raise AssertionError("feeds should be skipped in this test")

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr(
        "jobdigest.collect.load_portals",
        lambda: {
            "portals": [
                {
                    "name": "http-board",
                    "url": "https://http.example/jobs",
                    "enabled": True,
                    "parser": "generic_cards",
                    "fetch": "http",
                },
                {
                    "name": "spa-board",
                    "url": "https://spa.example/jobs",
                    "enabled": True,
                    "parser": "generic_cards",
                    "fetch": "dynamic",
                },
            ]
        },
    )
    monkeypatch.setattr(
        "jobdigest.collect.load_feeds",
        lambda: {"feeds": [{"name": "off", "url": "https://x", "enabled": False}]},
    )
    code = run(
        db_path=tmp_path / "jobs.db",
        sleep=lambda _: None,
        fetch_fn=fetch_fn,
        portal_fetch=("http",),
        portal_run_fn=portal_run_fn,
        feed_run_fn=boom_feed,
    )
    assert code == 0
    assert seen == ["http-board"]


def test_skip_ats_and_feeds_runs_portals_only(monkeypatch, tmp_path):
    seen = []

    def boom_ats(company, client, sleep):
        raise AssertionError("ATS must be skipped")

    def portal_run_fn(row):
        seen.append(row["name"])
        return PortalResult(status="ok", jobs=[], http_status=200)

    def boom_feed(row):
        raise AssertionError("feeds must be skipped")

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _enabled_portal)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _enabled_feed)
    code = run(
        db_path=tmp_path / "jobs.db",
        sleep=lambda _: None,
        fetch_fn=boom_ats,
        skip_ats=True,
        skip_feeds=True,
        portal_run_fn=portal_run_fn,
        feed_run_fn=boom_feed,
    )
    assert code == 0
    assert seen == ["dup-portal"]
