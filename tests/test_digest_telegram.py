import json
import re

from jobdigest.config import load_profile
from jobdigest.db import connect, finish_run, insert_run, upsert_job
from jobdigest.digest import run


def _collect_ok(conn):
    rid = insert_run(conn, "collect")
    finish_run(conn, rid, "ok", {})


def _pass_job(conn, profile, *, url: str, ext: str, title: str, company: str):
    action, jid = upsert_job(
        conn,
        profile,
        apply_url=url,
        hosted_url=url,
        source="greenhouse",
        external_id=ext,
        company_id=None,
        company=company,
        title=title,
        location_raw="Pune",
        description_text="Intern role in Pune",
        raw_json="{}",
        ats="greenhouse",
        employment_hint="intern",
    )
    assert action == "insert" and jid is not None
    return jid


def _item(job_id: int, relevant: bool) -> dict:
    return {
        "job_id": job_id,
        "score": 90 if relevant else 12,
        "relevant": relevant,
        "level_ok": relevant,
        "location_ok": True,
        "stack_ok": relevant,
        "why": "fit" if relevant else "no",
        "kill": None if relevant else "level",
    }


class _OkPost:
    status_code = 200
    text = '{"ok": true}'


def test_digest_score_failed_sends_alert(monkeypatch, tmp_path, capsys):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append(dict(json or {}))
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        _collect_ok(conn)
        _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/1",
            ext="1",
            title="Software Engineer Intern",
            company="Acme",
        )
    finally:
        conn.close()

    from jobdigest.openrouter import OpenRouterError

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        if json_mode:
            raise OpenRouterError("score down")
        raise AssertionError("summarizer should not run")

    code = run(db_path=db, chat_fn=chat_fn, telegram_http_post=http_post)
    _ = capsys.readouterr()
    assert code == 1
    assert posts[0]["text"] == "SCORE FAILED"


def test_digest_collect_failed_sends_alert(monkeypatch, tmp_path, capsys):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    connect(db).close()

    def chat_fn(*args, **kwargs):
        raise AssertionError("chat_fn should not be called")

    code = run(db_path=db, chat_fn=chat_fn, telegram_http_post=http_post)
    _ = capsys.readouterr()
    assert code == 1
    assert posts[0]["json"]["text"] == "COLLECT FAILED"
    assert "parse_mode" not in posts[0]["json"]


def test_digest_empty_guest_does_not_send(monkeypatch, tmp_path, capsys):
    posts = []

    def http_post(url, json=None, timeout=None):
        posts.append(1)
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        _collect_ok(conn)
    finally:
        conn.close()

    def chat_fn(*args, **kwargs):
        raise AssertionError("chat_fn should not be called")

    code = run(db_path=db, chat_fn=chat_fn, telegram_http_post=http_post)
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert posts == []


def test_digest_nonempty_sends_body(monkeypatch, tmp_path, capsys):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append(dict(json or {}))
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        _collect_ok(conn)
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/1",
            ext="1",
            title="Software Engineer Intern",
            company="Acme",
        )
    finally:
        conn.close()

    body = f"[id={jid}]\nAcme intern\nhttps://example.com/jobs/1\n"

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        if json_mode:
            user = messages[-1]["content"]
            batch_ids = [int(x) for x in re.findall(r"<<<JOB id=(\d+)>>>", user)]
            jobs = [_item(i, True) for i in batch_ids]
            return json.dumps({"jobs": jobs})
        return body

    code = run(db_path=db, chat_fn=chat_fn, telegram_http_post=http_post)
    captured = capsys.readouterr()
    assert code == 0
    assert "Acme" in captured.out
    assert len(posts) == 1
    assert "Acme" in posts[0]["text"]
    assert posts[0]["chat_id"] == "7"
    assert "parse_mode" not in posts[0]
