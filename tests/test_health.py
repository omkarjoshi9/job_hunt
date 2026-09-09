from datetime import datetime, timezone

from jobdigest.ats.base import FetchResult, FetchStatus, NormalizedJob
from jobdigest.collect import run
from jobdigest.db import (
    connect,
    finish_run,
    insert_portal_error,
    insert_run,
    update_company_fetch,
    upsert_company,
)
from jobdigest.health import HEALTHY, HealthReport, collect_health, format_health


def test_health_classifies_error_empty_zero_dead(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        err_id = upsert_company(conn, "boomco", "Boom", "greenhouse", "boom", True)
        empty_id = upsert_company(conn, "emptyco", "Empty", "lever", "empty", True)
        dead_id = upsert_company(conn, "deadco", "Dead", "ashby", "dead", True)
        update_company_fetch(
            conn, err_id, http_status=500, job_count=0, error="timeout", dead_streak=0
        )
        update_company_fetch(
            conn, empty_id, http_status=200, job_count=0, error=None, dead_streak=0
        )
        update_company_fetch(
            conn,
            dead_id,
            http_status=404,
            job_count=0,
            error="dead_token",
            dead_streak=2,
        )
        rid = insert_run(conn, "collect")
        insert_portal_error(
            conn,
            run_id=rid,
            portal_name="hasjob",
            url="https://hasjob.co/",
            error_class="parse",
            http_status=200,
            detail="0 cards",
        )
        finish_run(conn, rid, "ok", {})
        report = collect_health(conn)
        assert any("boomco" in line for line in report.errored)
        assert any("emptyco" in line for line in report.empty_boards)
        assert any("hasjob" in line for line in report.zero_cards)
        assert any("deadco" in line for line in report.dead_tokens)
        text = format_health(report)
        assert "ERRORS" in text
        assert "EMPTY" in text
        assert "0 CARDS" in text
        assert "DEAD TOKENS" in text
    finally:
        conn.close()


def test_health_dead_token_requires_streak_2(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        cid = upsert_company(conn, "almost", "Almost", "greenhouse", "x", True)
        update_company_fetch(
            conn,
            cid,
            http_status=404,
            job_count=0,
            error="dead_token",
            dead_streak=1,
        )
        report = collect_health(conn)
        assert report.dead_tokens == []
    finally:
        conn.close()


def test_format_health_healthy():
    assert format_health(HealthReport()) == HEALTHY


def _enabled():
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


def test_collect_dead_streak_increment_and_reset(monkeypatch, tmp_path):
    def dead_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.DEAD_TOKEN, 404, [], error="dead_token")

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=dead_fn) == 0
    conn = connect(db)
    try:
        row = conn.execute("SELECT dead_streak, last_error FROM companies").fetchone()
        assert int(row["dead_streak"]) == 1
        assert row["last_error"] == "dead_token"
    finally:
        conn.close()
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=dead_fn) == 0
    conn = connect(db)
    try:
        row = conn.execute("SELECT dead_streak FROM companies").fetchone()
        assert int(row["dead_streak"]) == 2
        report = collect_health(conn)
        assert any("example" in line for line in report.dead_tokens)
    finally:
        conn.close()

    job = NormalizedJob(
        title="Software Engineer Intern",
        company="Example Corp",
        location_raw="Pune",
        apply_url="https://example.com/jobs/1",
        hosted_url="https://example.com/jobs/1",
        employment_hint="intern",
        workplace_hint=None,
        description_text="Intern role in Pune",
        source="example",
        ats="greenhouse",
        external_id="1",
        raw_json="{}",
        fetched_at=datetime.now(timezone.utc),
    )

    def ok_jobs(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, [job])

    assert run(db_path=db, sleep=lambda _: None, fetch_fn=ok_jobs) == 0
    conn = connect(db)
    try:
        row = conn.execute("SELECT dead_streak, last_error FROM companies").fetchone()
        assert int(row["dead_streak"]) == 0
        assert row["last_error"] is None
    finally:
        conn.close()


def test_collect_prints_health_footer_when_unhealthy(monkeypatch, tmp_path, capsys):
    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.ERROR, 500, [], error="boom")

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    code = run(db_path=db, sleep=lambda _: None, fetch_fn=fetch_fn)
    captured = capsys.readouterr()
    assert code == 1
    assert "ERRORS" in captured.out
    assert "example" in captured.out


def test_collect_error_does_not_bump_miss_count(monkeypatch, tmp_path):
    job = NormalizedJob(
        title="Software Engineer Intern",
        company="Example Corp",
        location_raw="Pune",
        apply_url="https://example.com/jobs/1",
        hosted_url="https://example.com/jobs/1",
        employment_hint="intern",
        workplace_hint=None,
        description_text="Intern role in Pune",
        source="example",
        ats="greenhouse",
        external_id="1",
        raw_json="{}",
        fetched_at=datetime.now(timezone.utc),
    )

    def ok_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, [job])

    def err_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.ERROR, 500, [], error="boom")

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=ok_fn) == 0
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=err_fn) == 1
    conn = connect(db)
    try:
        row = conn.execute("SELECT miss_count, is_active FROM jobs").fetchone()
        assert int(row["miss_count"]) == 0
        assert int(row["is_active"]) == 1
        streak = conn.execute("SELECT dead_streak FROM companies").fetchone()
        assert int(streak["dead_streak"]) == 0
    finally:
        conn.close()
