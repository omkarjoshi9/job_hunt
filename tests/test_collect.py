import json
from datetime import datetime, timezone

from jobdigest.ats.base import FetchResult, FetchStatus, NormalizedJob
from jobdigest.collect import run
from jobdigest.db import connect


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
    db = tmp_path / "jobs.db"
    code = run(db_path=db, sleep=lambda _: None, fetch_fn=fetch_fn)
    captured = capsys.readouterr()
    assert code == 1
    assert "COLLECT FAILED" in captured.err or "COLLECT FAILED" in captured.out


def test_collect_default_yaml_no_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBDIGEST_ENABLE", "")
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


def test_collect_jobdigest_enable_fetches_disabled_yaml(monkeypatch, tmp_path):
    jobs = [_intern_job("https://example.com/jobs/1", "1")]
    calls = {"n": 0}

    def fetch_fn(company, client, sleep):
        _ = client, sleep
        calls["n"] += 1
        assert company.id == "example"
        return FetchResult(FetchStatus.OK, 200, jobs)

    monkeypatch.setenv("JOBDIGEST_ENABLE", "example")
    monkeypatch.setattr("jobdigest.collect.load_companies", _disabled_companies)
    db = tmp_path / "jobs.db"
    code = run(db_path=db, sleep=lambda _: None, fetch_fn=fetch_fn)
    assert code == 0
    assert calls["n"] == 1
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        assert count == 1
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
