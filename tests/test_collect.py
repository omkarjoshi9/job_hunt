import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from jobdigest.ats.base import CompanyConfig, FetchResult, FetchStatus, NormalizedJob
from jobdigest.collect import _board_url, run
from jobdigest.db import connect, update_company_fetch, upsert_company

ROOT = Path(__file__).resolve().parents[1]


def _intern_job(url: str, ext: str) -> NormalizedJob:
    return NormalizedJob(
        title="Software Engineer Intern",
        company="Example Corp",
        location_raw="Pune",
        apply_url=url,
        hosted_url=url,
        employment_hint="intern",
        workplace_hint=None,
        description_text="Intern role in Pune",
        source="example",
        ats="greenhouse",
        external_id=ext,
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


def test_collect_upsert_twice_does_not_double(monkeypatch, tmp_path):
    jobs = [
        _intern_job("https://example.com/jobs/1", "1"),
        _intern_job("https://example.com/jobs/2", "2"),
    ]

    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, jobs)

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    code1 = run(db_path=db, sleep=lambda _: None, fetch_fn=fetch_fn)
    code2 = run(db_path=db, sleep=lambda _: None, fetch_fn=fetch_fn)
    assert code1 == 0
    assert code2 == 0
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        runs = conn.execute(
            "SELECT kind, status, stats_json FROM runs ORDER BY id"
        ).fetchall()
        first = json.loads(runs[0]["stats_json"])
        second = json.loads(runs[1]["stats_json"])
        assert count == 2
        assert first["inserted"] == 2
        assert second["inserted"] == 0
        assert second["updated"] == 2
        assert runs[-1]["kind"] == "collect"
        assert runs[-1]["status"] == "ok"
    finally:
        conn.close()


def test_collect_all_error_fails(monkeypatch, tmp_path, capsys):
    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.ERROR, 500, [], error="boom")

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    code = run(db_path=db, sleep=lambda _: None, fetch_fn=fetch_fn)
    captured = capsys.readouterr()
    assert code == 1
    assert "COLLECT FAILED" in captured.err or "COLLECT FAILED" in captured.out


def test_collect_default_yaml_no_enabled(monkeypatch, tmp_path):
    payload = yaml.safe_load(
        (ROOT / "config" / "companies.example.yaml").read_text(encoding="utf-8")
    )
    monkeypatch.setattr("jobdigest.collect.load_companies", lambda: payload)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    code = run(db_path=db, sleep=lambda _: None)
    assert code == 0
    conn = connect(db)
    try:
        jobs = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        run_row = conn.execute("SELECT status FROM runs").fetchone()
        assert jobs == 0
        assert run_row["status"] == "ok"
    finally:
        conn.close()


def _disabled_companies():
    return {
        "companies": [
            {
                "id": "example",
                "name": "Example Corp",
                "ats": "greenhouse",
                "token": "example",
                "enabled": False,
            }
        ]
    }


class _OkPost:
    status_code = 200
    text = '{"ok": true}'


def test_collect_disabled_yaml_is_not_fetched(monkeypatch, tmp_path):
    jobs = [_intern_job("https://example.com/jobs/1", "1")]
    calls = {"n": 0}

    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        calls["n"] += 1
        return FetchResult(FetchStatus.OK, 200, jobs)

    monkeypatch.setattr("jobdigest.collect.load_companies", _disabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    code = run(db_path=db, sleep=lambda _: None, fetch_fn=fetch_fn)
    assert code == 0
    assert calls["n"] == 0
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        assert count == 0
    finally:
        conn.close()


def test_collect_fail_sends_telegram(monkeypatch, tmp_path, capsys):
    posts = []

    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.ERROR, 500, [], error="boom")

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    code = run(
        db_path=db,
        sleep=lambda _: None,
        fetch_fn=fetch_fn,
        telegram_http_post=http_post,
    )
    _ = capsys.readouterr()
    assert code == 1
    assert len(posts) == 1
    assert posts[0]["url"] == "https://api.telegram.org/bottok/sendMessage"
    assert posts[0]["json"]["chat_id"] == "42"
    assert posts[0]["json"]["text"] == "COLLECT FAILED"
    assert "parse_mode" not in posts[0]["json"]


def test_collect_persists_workplace_hint(monkeypatch, tmp_path):
    jobs = [
        NormalizedJob(
            title="Software Engineer Intern",
            company="Example Corp",
            location_raw="Pune",
            apply_url="https://example.com/jobs/hint",
            hosted_url="https://example.com/jobs/hint",
            employment_hint="Intern",
            workplace_hint="hybrid",
            description_text="Intern role in Pune",
            source="example",
            ats="lever",
            external_id="hint-1",
            raw_json="{}",
            fetched_at=datetime.now(timezone.utc),
        )
    ]

    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, jobs)

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=fetch_fn) == 0
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT employment_hint, workplace_hint FROM jobs"
        ).fetchone()
        assert row["employment_hint"] == "Intern"
        assert row["workplace_hint"] == "hybrid"
    finally:
        conn.close()


def _example_company() -> CompanyConfig:
    return CompanyConfig(
        id="example",
        name="Example Corp",
        ats="greenhouse",
        token="example",
        enabled=True,
    )


def test_update_company_fetch_writes_resolved_url(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        cid = upsert_company(conn, "example", "Example Corp", "greenhouse", "example", True)
        url = _board_url(_example_company())
        update_company_fetch(
            conn,
            cid,
            http_status=200,
            job_count=1,
            error=None,
            resolved_url=url,
        )
        row = conn.execute(
            "SELECT resolved_url FROM companies WHERE id = ?", (cid,)
        ).fetchone()
        assert row["resolved_url"] == url
    finally:
        conn.close()


def test_collect_sets_resolved_url_on_ok(monkeypatch, tmp_path):
    jobs = [_intern_job("https://example.com/jobs/resolved", "resolved")]

    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, jobs)

    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=fetch_fn) == 0
    conn = connect(db)
    try:
        row = conn.execute("SELECT resolved_url FROM companies").fetchone()
        assert row["resolved_url"] == _board_url(_example_company())
    finally:
        conn.close()


def test_collect_sets_resolved_url_on_empty_dead_error(monkeypatch, tmp_path):
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", lambda: {"portals": []})
    monkeypatch.setattr("jobdigest.collect.load_feeds", lambda: {"feeds": []})
    expected = _board_url(_example_company())
    cases = [
        (FetchStatus.EMPTY_BOARD, 200, None, 0),
        (FetchStatus.DEAD_TOKEN, 404, "gone", 0),
        (FetchStatus.ERROR, 500, "boom", 1),
    ]
    for status, http_status, error, want_code in cases:
        db = tmp_path / f"jobs-{status.value}.db"

        def fetch_fn(company, client, sleep, *, _status=status, _http=http_status, _err=error):
            _ = company, client, sleep
            return FetchResult(_status, _http, [], error=_err)

        code = run(db_path=db, sleep=lambda _: None, fetch_fn=fetch_fn)
        assert code == want_code
        conn = connect(db)
        try:
            row = conn.execute("SELECT resolved_url FROM companies").fetchone()
            assert row["resolved_url"] == expected, status
        finally:
            conn.close()

