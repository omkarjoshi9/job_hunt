import json
import re

from datetime import datetime, timedelta, timezone
from pathlib import Path

from jobdigest.config import load_profile
from jobdigest.db import connect, finish_run, insert_run, last_digest_job_ids, upsert_job, upsert_score
from jobdigest.digest import _guest_dicts, run
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
                title = f"{companies[i]} Software Intern Role"
                company = companies[i]
            else:
                title = f"ZZJunkTitle{i:02d} Software Intern"
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
    assert "Why you:" in captured.out
    for title in junk_titles:
        assert title not in captured.out
    conn = connect(db)
    try:
        stored = last_digest_job_ids(conn)
    finally:
        conn.close()
    assert stored
    assert set(stored) <= relevant_ids


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
    conn = connect(db)
    try:
        assert last_digest_job_ids(conn) == []
    finally:
        conn.close()


def test_guest_dicts_keeps_why_and_chips(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/why-1",
            ext="why-1",
            title="Software Engineer Intern",
            company="Acme",
        )
        upsert_score(
            conn,
            jid,
            "m",
            "v",
            True,
            91.0,
            {
                "why": "intern python",
                "stack_ok": True,
                "level_ok": True,
                "location_ok": True,
                "kill": None,
            },
        )
        row = conn.execute(
            """
            SELECT jobs.*, scores.score AS score, scores.reasons_json AS reasons_json
            FROM jobs
            JOIN scores ON scores.job_id = jobs.id
            """
        ).fetchone()
        guest = _guest_dicts([row], now=now)
    finally:
        conn.close()
    assert guest[0]["why"] == "intern python"
    assert guest[0]["source_chip"] == "ats"
    assert guest[0]["score"] == 91.0


def test_guest_dicts_gap_location(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/gap-1",
            ext="gap-1",
            title="Software Engineer Intern",
            company="Acme",
        )
        upsert_score(
            conn,
            jid,
            "m",
            "v",
            True,
            80.0,
            {
                "why": "intern python",
                "stack_ok": True,
                "level_ok": True,
                "location_ok": False,
                "kill": None,
            },
        )
        row = conn.execute(
            """
            SELECT jobs.*, scores.score AS score, scores.reasons_json AS reasons_json
            FROM jobs
            JOIN scores ON scores.job_id = jobs.id
            """
        ).fetchone()
        guest = _guest_dicts([row], now=now)
    finally:
        conn.close()
    assert "location" in guest[0]["gap_labels"]
    assert guest[0]["location_ok"] is False


def test_guest_dicts_age_overlay_sets_chip(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(days=91)).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/age-91",
            ext="age-91",
            title="Software Engineer Intern",
            company="Acme",
        )
        conn.execute(
            """
            UPDATE jobs
            SET first_seen_at = ?, legitimacy_flag = ?, legitimacy_reasons_json = ?
            WHERE id = ?
            """,
            (old, "ok", "[]", jid),
        )
        conn.commit()
        upsert_score(
            conn,
            jid,
            "m",
            "v",
            True,
            91.0,
            {
                "why": "intern python",
                "stack_ok": True,
                "level_ok": True,
                "location_ok": True,
                "kill": None,
            },
        )
        row = conn.execute(
            """
            SELECT jobs.*, scores.score AS score, scores.reasons_json AS reasons_json
            FROM jobs
            JOIN scores ON scores.job_id = jobs.id
            """
        ).fetchone()
        guest = _guest_dicts([row], now=now)
    finally:
        conn.close()
    assert guest[0]["legitimacy_flag"] == "suspect"
    assert guest[0]["legitimacy_chip"] == "suspect:90d"


def test_digest_source_does_not_import_blurb():
    root = Path(__file__).resolve().parents[1] / "src" / "jobdigest"
    digest = (root / "digest.py").read_text(encoding="utf-8")
    score = (root / "score.py").read_text(encoding="utf-8")
    assert "jobdigest.blurb" not in digest
    assert "draft_blurb" not in digest
    assert "jobdigest.contacts" not in digest
    assert "draft_blurb" not in score
