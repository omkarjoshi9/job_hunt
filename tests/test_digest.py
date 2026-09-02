import json
import re

from jobdigest.config import load_profile
from jobdigest.db import connect, insert_run, finish_run, upsert_job
from jobdigest.digest import run
from jobdigest.openrouter import OpenRouterError


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


def test_digest_no_collect_fails_without_chat(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    connect(db).close()

    def chat_fn(*args, **kwargs):
        raise AssertionError("chat_fn should not be called")

    code = run(db_path=db, chat_fn=chat_fn)
    captured = capsys.readouterr()
    assert code == 1
    assert "COLLECT FAILED" in captured.out
    assert "COLLECT FAILED" in captured.err


def test_digest_score_fail_skips_summarizer(tmp_path, capsys):
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

    calls = {"score": 0, "sum": 0}

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        if json_mode:
            calls["score"] += 1
            raise OpenRouterError("score down")
        calls["sum"] += 1
        return "should not run"

    code = run(db_path=db, chat_fn=chat_fn)
    captured = capsys.readouterr()
    assert code == 1
    assert calls["score"] == 1
    assert calls["sum"] == 0
    assert "SCORE FAILED" in captured.out
    assert "SCORE FAILED" in captured.err


def test_digest_three_relevant_not_junk(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    junk_titles = []
    try:
        _collect_ok(conn)
        ids = []
        companies = ["Acme", "Globex", "Initech"]
        for i in range(13):
            if i < 3:
                title = f"{companies[i]} Intern Role"
                company = companies[i]
            else:
                title = f"ZZJunkTitle{i:02d} Intern"
                company = f"JunkCo{i:02d}"
                junk_titles.append(title)
            ids.append(
                _pass_job(
                    conn,
                    profile,
                    url=f"https://example.com/jobs/{i+1}",
                    ext=str(i + 1),
                    title=title,
                    company=company,
                )
            )
        relevant_ids = set(ids[:3])
    finally:
        conn.close()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        if json_mode:
            user = messages[-1]["content"]
            batch_ids = [int(x) for x in re.findall(r"<<<JOB id=(\d+)>>>", user)]
            jobs = [_item(jid, jid in relevant_ids) for jid in batch_ids]
            return json.dumps({"jobs": jobs})
        return (
            "[id=" + str(ids[0]) + "]\nAcme intern\nhttps://example.com/jobs/1\n"
            "[id=" + str(ids[1]) + "]\nGlobex intern\nhttps://example.com/jobs/2\n"
            "[id=" + str(ids[2]) + "]\nInitech intern\nhttps://example.com/jobs/3\n"
        )

    code = run(db_path=db, chat_fn=chat_fn)
    captured = capsys.readouterr()
    assert code == 0
    assert "Acme" in captured.out
    assert "Globex" in captured.out
    assert "Initech" in captured.out
    for title in junk_titles:
        assert title not in captured.out


def test_digest_zero_pass_jobs_silent(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        _collect_ok(conn)
    finally:
        conn.close()

    def chat_fn(*args, **kwargs):
        raise AssertionError("chat_fn should not be called")

    code = run(db_path=db, chat_fn=chat_fn)
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
