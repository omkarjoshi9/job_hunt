from datetime import datetime, timezone
from pathlib import Path

import yaml

from jobdigest.ats.base import NormalizedJob
from jobdigest.cli import main
from jobdigest.collect import run
from jobdigest.db import connect
from jobdigest.portals import PortalResult

ROOT = Path(__file__).resolve().parents[1]


def _intern_job(*, source: str = "naukri-intern-india") -> NormalizedJob:
    return NormalizedJob(
        title="Software Engineer Intern",
        company="Example Corp",
        location_raw="Pune",
        apply_url="https://www.naukri.com/job/mp-1",
        hosted_url="https://www.naukri.com/job/mp-1",
        employment_hint="intern",
        workplace_hint=None,
        description_text="Intern role in Pune",
        source=source,
        ats="marketplace",
        external_id="mp-1",
        raw_json="{}",
        fetched_at=datetime.now(timezone.utc),
    )


def _enabled_marketplace():
    return {
        "marketplaces": [
            {
                "name": "naukri-intern-india",
                "site": "naukri",
                "enabled": True,
                "search_term": "software intern",
            }
        ]
    }


def test_collect_marketplace_upserts_and_sweeps(monkeypatch, tmp_path):
    seen = []

    def boom_ats(company, client, sleep):
        raise AssertionError("ATS must be skipped")

    def boom_portal(row):
        raise AssertionError("portals must be skipped")

    def boom_feed(row):
        raise AssertionError("feeds must be skipped")

    def marketplace_run_fn(row):
        seen.append(row["name"])
        return PortalResult(status="ok", jobs=[_intern_job()], http_status=200)

    monkeypatch.setattr("jobdigest.collect.load_companies", lambda: {"companies": []})
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    monkeypatch.setattr("jobdigest.collect.load_marketplaces", _enabled_marketplace)
    db = tmp_path / "jobs.db"
    code = run(
        db_path=db,
        sleep=lambda _: None,
        fetch_fn=boom_ats,
        skip_ats=True,
        skip_portals=True,
        skip_feeds=True,
        portal_run_fn=boom_portal,
        feed_run_fn=boom_feed,
        marketplace_run_fn=marketplace_run_fn,
    )
    assert code == 0
    assert seen == ["naukri-intern-india"]
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT source, ats, is_active FROM jobs WHERE external_id = ?",
            ("mp-1",),
        ).fetchone()
        assert row["source"] == "naukri-intern-india"
        assert row["ats"] == "marketplace"
        assert row["is_active"] == 1
    finally:
        conn.close()

    def empty_run(row):
        return PortalResult(status="ok", jobs=[], http_status=200)

    code = run(
        db_path=db,
        sleep=lambda _: None,
        fetch_fn=boom_ats,
        skip_ats=True,
        skip_portals=True,
        skip_feeds=True,
        portal_run_fn=boom_portal,
        feed_run_fn=boom_feed,
        marketplace_run_fn=empty_run,
    )
    assert code == 0
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT miss_count, is_active FROM jobs WHERE external_id = ?",
            ("mp-1",),
        ).fetchone()
        assert row["miss_count"] == 1
        assert row["is_active"] == 1
    finally:
        conn.close()

    code = run(
        db_path=db,
        sleep=lambda _: None,
        fetch_fn=boom_ats,
        skip_ats=True,
        skip_portals=True,
        skip_feeds=True,
        portal_run_fn=boom_portal,
        feed_run_fn=boom_feed,
        marketplace_run_fn=empty_run,
    )
    assert code == 0
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT miss_count, is_active, close_reason FROM jobs WHERE external_id = ?",
            ("mp-1",),
        ).fetchone()
        assert row["miss_count"] >= 2
        assert row["is_active"] == 0
        assert row["close_reason"] == "unseen_board"
    finally:
        conn.close()


def test_ats_only_skips_marketplaces(monkeypatch, tmp_path):
    def boom_mp(row):
        raise AssertionError("marketplaces must be skipped with ats-only")

    monkeypatch.setattr("jobdigest.collect.load_companies", lambda: {"companies": []})
    monkeypatch.setattr("jobdigest.collect.load_marketplaces", _enabled_marketplace)
    code = run(
        db_path=tmp_path / "jobs.db",
        sleep=lambda _: None,
        fetch_fn=lambda company, client, sleep: (_ for _ in ()).throw(
            AssertionError("no ATS rows")
        ),
        ats_only=True,
        marketplace_run_fn=boom_mp,
    )
    assert code == 0


def test_cli_collect_skip_marketplaces(monkeypatch, tmp_path):
    monkeypatch.setattr("jobdigest.collect.load_companies", lambda: {"companies": []})
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    assert (
        main(
            [
                "collect",
                "--skip-ats",
                "--skip-portals",
                "--skip-feeds",
                "--skip-marketplaces",
                "--db",
                str(db),
            ]
        )
        == 0
    )


def test_git_yaml_marketplaces_disabled():
    data = yaml.safe_load(
        (ROOT / "config" / "marketplaces.example.yaml").read_text(encoding="utf-8")
    )
    rows = data["marketplaces"]
    assert all(not row.get("enabled") for row in rows)
    sites = {row["site"] for row in rows}
    assert sites == {
        "naukri",
        "indeed",
        "linkedin",
        "glassdoor",
        "google",
        "zip_recruiter",
        "bayt",
        "bdjobs",
    }
    naukri = next(row for row in rows if row["site"] == "naukri")
    assert naukri["linkedin_fetch_description"] is True
