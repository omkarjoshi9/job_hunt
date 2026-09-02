import json
import re

from jobdigest.config import load_models, load_profile
from jobdigest.db import connect, guest_rows, insert_run, finish_run, upsert_job
from jobdigest.openrouter import OpenRouterError
from jobdigest.score import run


def _collect_ok(conn):
    rid = insert_run(conn, "collect")
    finish_run(conn, rid, "ok", {})


def _pass_job(conn, profile, *, url: str, ext: str, title: str, company: str, raw_json: str = "{}"):
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
        raw_json=raw_json,
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
        "why": "intern fit" if relevant else "not intern/new-grad India",
        "kill": None if relevant else "level",
    }


def test_score_persists_relevant_and_false(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    models = load_models()
    try:
        _collect_ok(conn)
        ids = []
        for i in range(13):
            title = "Software Engineer Intern" if i < 3 else f"Campus Intern ZZJunk{i:02d}"
            company = f"Co{i:02d}"
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
        assert json_mode is True
        user = messages[-1]["content"]
        batch_ids = [int(x) for x in re.findall(r"<<<JOB id=(\d+)>>>", user)]
        jobs = [_item(jid, jid in relevant_ids) for jid in batch_ids]
        return json.dumps({"jobs": jobs})

    assert run(db_path=db, chat_fn=chat_fn) == 0
    conn = connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM scores").fetchone()["n"]
        guests = guest_rows(
            conn, models.score_model, models.score_prompt_version, profile.digest_max
        )
        assert n == 13
        assert len(guests) == 3
        assert {int(g["id"]) for g in guests} == relevant_ids
    finally:
        conn.close()


def test_score_drops_unknown_job_id(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        real_id = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/only",
            ext="only",
            title="Software Engineer Intern",
            company="Acme",
        )
    finally:
        conn.close()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        return json.dumps(
            {"jobs": [_item(real_id, True), _item(999999, True)]}
        )

    assert run(db_path=db, chat_fn=chat_fn) == 0
    conn = connect(db)
    try:
        rows = conn.execute("SELECT job_id FROM scores").fetchall()
        assert [int(r["job_id"]) for r in rows] == [real_id]
    finally:
        conn.close()


def test_score_openrouter_error(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
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

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        raise OpenRouterError("boom")

    assert run(db_path=db, chat_fn=chat_fn) == 1
    assert "SCORE FAILED" in capsys.readouterr().err


def test_score_fenced_prompt_omits_raw_json(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/1",
            ext="1",
            title="Software Engineer Intern",
            company="Acme",
            raw_json='{"secret-raw":1}',
        )
    finally:
        conn.close()

    seen: dict[str, str] = {}

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[-1]["content"]
        return json.dumps({"jobs": [_item(jid, True)]})

    assert run(db_path=db, chat_fn=chat_fn) == 0
    assert "<<<JOB id=" in seen["user"]
    assert "secret-raw" not in seen["user"]
    assert "secret-raw" not in seen["system"]
    blob = seen["system"] + seen["user"]
    assert "Omkar" not in blob
    assert "8.88" not in blob
    assert "software engineering" in seen["system"].lower()


def test_score_ops_associate_not_guest_even_if_model_says_yes(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    models = load_models()
    try:
        _collect_ok(conn)
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/ops",
            ext="ops",
            title="Operations Associate, Apprenticeship",
            company="Stripe",
        )
        conn.execute("UPDATE jobs SET hard_status = 'pass' WHERE id = ?", (jid,))
        conn.commit()
    finally:
        conn.close()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        return json.dumps(
            {
                "jobs": [
                    {
                        "job_id": jid,
                        "score": 85,
                        "relevant": True,
                        "level_ok": True,
                        "location_ok": True,
                        "stack_ok": True,
                        "why": "ops associate new grad",
                        "kill": None,
                    }
                ]
            }
        )

    assert run(db_path=db, chat_fn=chat_fn) == 0
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT relevant, score FROM scores WHERE job_id = ?", (jid,)
        ).fetchone()
        guests = guest_rows(
            conn, models.score_model, models.score_prompt_version, profile.digest_max
        )
        assert int(row["relevant"]) == 0
        assert float(row["score"]) == 0.0
        assert [int(g["id"]) for g in guests] == []
    finally:
        conn.close()


def test_score_stack_ok_false_not_guest(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    models = load_models()
    try:
        _collect_ok(conn)
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/swe",
            ext="swe",
            title="Software Engineer Intern",
            company="Acme",
        )
    finally:
        conn.close()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        return json.dumps(
            {
                "jobs": [
                    {
                        "job_id": jid,
                        "score": 90,
                        "relevant": True,
                        "level_ok": True,
                        "location_ok": True,
                        "stack_ok": False,
                        "why": "intern but stack no",
                        "kill": None,
                    }
                ]
            }
        )

    assert run(db_path=db, chat_fn=chat_fn) == 0
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT relevant, score FROM scores WHERE job_id = ?", (jid,)
        ).fetchone()
        guests = guest_rows(
            conn, models.score_model, models.score_prompt_version, profile.digest_max
        )
        assert int(row["relevant"]) == 0
        assert float(row["score"]) == 0.0
        assert list(guests) == []
    finally:
        conn.close()


def test_score_senior_swe_not_guest_even_if_model_says_yes(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    models = load_models()
    try:
        _collect_ok(conn)
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/senior",
            ext="senior",
            title="Senior Software Engineer (App-Framework)",
            company="Databricks",
        )
        conn.execute("UPDATE jobs SET hard_status = 'pass' WHERE id = ?", (jid,))
        conn.commit()
    finally:
        conn.close()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        return json.dumps(
            {
                "jobs": [
                    {
                        "job_id": jid,
                        "score": 85,
                        "relevant": True,
                        "level_ok": True,
                        "location_ok": True,
                        "stack_ok": True,
                        "why": "India SWE",
                        "kill": None,
                    }
                ]
            }
        )

    assert run(db_path=db, chat_fn=chat_fn) == 0
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT relevant, score FROM scores WHERE job_id = ?", (jid,)
        ).fetchone()
        guests = guest_rows(
            conn, models.score_model, models.score_prompt_version, profile.digest_max
        )
        assert int(row["relevant"]) == 0
        assert float(row["score"]) == 0.0
        assert [int(g["id"]) for g in guests] == []
    finally:
        conn.close()
