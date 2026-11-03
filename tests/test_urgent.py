import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    connect,
    finish_run,
    guest_rows,
    insert_run,
    last_digest_job_ids,
    relevant_unapplied_jobs,
    stamp_notified,
    upsert_job,
    upsert_score,
)
from jobdigest.digest import maybe_send_urgent, run
from jobdigest.telegram import PAGER_TEXT

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _collect_ok(conn):
    rid = insert_run(conn, "collect")
    finish_run(conn, rid, "ok", {})


STRONG_JD = (
    "Responsibilities: you will build backend services in Python and FastAPI, "
    "ship APIs on AWS with Docker, and work on LangChain RAG pipelines and security "
    "tooling. We hire freshers and new grads in Pune. " * 3
)


def _pass_job(conn, profile, *, url: str, ext: str, company: str, first_seen: str | None = None):
    action, jid = upsert_job(
        conn,
        profile,
        apply_url=url,
        hosted_url=url,
        source="greenhouse",
        external_id=ext,
        company_id=None,
        company=company,
        title="Backend Software Engineer New Grad",
        location_raw="Pune",
        description_text=STRONG_JD,
        raw_json="{}",
        ats="greenhouse",
        employment_hint="intern",
    )
    assert action == "insert" and jid is not None
    if first_seen is not None:
        conn.execute(
            "UPDATE jobs SET first_seen_at = ? WHERE id = ?",
            (first_seen, jid),
        )
        conn.commit()
    return jid


def _score(conn, jid: int, score: float):
    models = load_models()
    upsert_score(
        conn,
        jid,
        models.score_model,
        models.score_prompt_version,
        True,
        score,
        {
            "why": "intern python",
            "stack_ok": True,
            "level_ok": True,
            "location_ok": True,
            "kill": None,
        },
    )


def _profile(**kwargs):
    return replace(load_profile(), **kwargs)


class _OkPost:
    status_code = 200
    text = '{"ok": true}'

    def json(self):
        return json.loads(self.text)


def _http(posts):
    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    return http_post


def test_watchlist_exact_casefold_not_substring(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    conn = connect(tmp_path / "jobs.db")
    base = load_profile()
    posts = []
    try:
        jid = _pass_job(
            conn,
            base,
            url="https://example.com/jobs/fi",
            ext="fi",
            company="Fiserv",
            first_seen=_iso(NOW - timedelta(hours=2)),
        )
        _score(conn, jid, 50.0)
        n = maybe_send_urgent(
            conn,
            models=load_models(),
            profile=_profile(watchlist=["fi"], urgent_score_min=85, urgent_daily_cap=3),
            now=NOW,
            http_post=_http(posts),
        )
        row = conn.execute("SELECT notified_at FROM jobs WHERE id = ?", (jid,)).fetchone()
    finally:
        conn.close()
    assert len(n) == 0
    assert row["notified_at"] is None
    assert posts == []


def test_score_threshold_85(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    conn = connect(tmp_path / "jobs.db")
    base = load_profile()
    posts = []
    try:
        low = _pass_job(
            conn,
            base,
            url="https://example.com/jobs/low",
            ext="low",
            company="Acme",
            first_seen=_iso(NOW - timedelta(hours=2)),
        )
        high = _pass_job(
            conn,
            base,
            url="https://example.com/jobs/high",
            ext="high",
            company="Globex",
            first_seen=_iso(NOW - timedelta(hours=2)),
        )
        _score(conn, low, 84.0)
        _score(conn, high, 85.0)
        n = maybe_send_urgent(
            conn,
            models=load_models(),
            profile=_profile(watchlist=[], urgent_score_min=85, urgent_daily_cap=3),
            now=NOW,
            http_post=_http(posts),
        )
        kinds = {
            int(r["id"]): r["notify_kind"]
            for r in conn.execute(
                "SELECT id, notify_kind FROM jobs WHERE id IN (?, ?)", (low, high)
            ).fetchall()
        }
    finally:
        conn.close()
    assert len(n) == 1
    assert kinds[low] is None
    assert kinds[high] == "urgent"


def test_watchlist_bypasses_threshold(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    conn = connect(tmp_path / "jobs.db")
    base = load_profile()
    posts = []
    try:
        jid = _pass_job(
            conn,
            base,
            url="https://example.com/jobs/wl",
            ext="wl",
            company="Stripe",
            first_seen=_iso(NOW - timedelta(hours=2)),
        )
        _score(conn, jid, 40.0)
        n = maybe_send_urgent(
            conn,
            models=load_models(),
            profile=_profile(watchlist=["stripe"], urgent_score_min=85, urgent_daily_cap=3),
            now=NOW,
            http_post=_http(posts),
        )
        kind = conn.execute(
            "SELECT notify_kind FROM jobs WHERE id = ?", (jid,)
        ).fetchone()["notify_kind"]
    finally:
        conn.close()
    assert len(n) == 1
    assert kind == "urgent"
    assert any(p["url"].endswith("sendMessage") for p in posts)
    assert not any(p["json"].get("text") == PAGER_TEXT for p in posts)


def test_lookback_36h(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    conn = connect(tmp_path / "jobs.db")
    base = load_profile()
    posts = []
    try:
        old = _pass_job(
            conn,
            base,
            url="https://example.com/jobs/old",
            ext="old",
            company="OldCo",
            first_seen=_iso(NOW - timedelta(hours=37)),
        )
        fresh = _pass_job(
            conn,
            base,
            url="https://example.com/jobs/fresh",
            ext="fresh",
            company="FreshCo",
            first_seen=_iso(NOW - timedelta(hours=35)),
        )
        _score(conn, old, 90.0)
        _score(conn, fresh, 90.0)
        n = maybe_send_urgent(
            conn,
            models=load_models(),
            profile=_profile(watchlist=[], urgent_score_min=85, urgent_daily_cap=3),
            now=NOW,
            http_post=_http(posts),
        )
        old_n = conn.execute(
            "SELECT notified_at FROM jobs WHERE id = ?", (old,)
        ).fetchone()["notified_at"]
        fresh_k = conn.execute(
            "SELECT notify_kind FROM jobs WHERE id = ?", (fresh,)
        ).fetchone()["notify_kind"]
    finally:
        conn.close()
    assert len(n) == 1
    assert old_n is None
    assert fresh_k == "urgent"


def test_daily_cap_3_ist(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    conn = connect(tmp_path / "jobs.db")
    base = load_profile()
    posts = []
    try:
        ids = []
        for i in range(4):
            jid = _pass_job(
                conn,
                base,
                url=f"https://example.com/jobs/c{i}",
                ext=f"c{i}",
                company=f"Co{i}",
                first_seen=_iso(NOW - timedelta(hours=1)),
            )
            _score(conn, jid, 90.0)
            ids.append(jid)
        n = maybe_send_urgent(
            conn,
            models=load_models(),
            profile=_profile(watchlist=[], urgent_score_min=85, urgent_daily_cap=3),
            now=NOW,
            http_post=_http(posts),
        )
        stamped = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE notify_kind = 'urgent'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert len(n) == 3
    assert stamped == 3
    assert len([p for p in posts if p["url"].endswith("sendMessage")]) == 3


def test_mute_does_not_stamp(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    conn = connect(tmp_path / "jobs.db")
    base = load_profile()
    try:
        jid = _pass_job(
            conn,
            base,
            url="https://example.com/jobs/mute",
            ext="mute",
            company="Acme",
            first_seen=_iso(NOW - timedelta(hours=1)),
        )
        _score(conn, jid, 90.0)
        n = maybe_send_urgent(
            conn,
            models=load_models(),
            profile=_profile(watchlist=[], urgent_score_min=85, urgent_daily_cap=3),
            now=NOW,
            http_post=None,
        )
        row = conn.execute(
            "SELECT notified_at FROM jobs WHERE id = ?", (jid,)
        ).fetchone()
    finally:
        conn.close()
    assert len(n) == 0
    assert row["notified_at"] is None


def test_stamp_after_real_send(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    conn = connect(tmp_path / "jobs.db")
    base = load_profile()
    posts = []
    try:
        jid = _pass_job(
            conn,
            base,
            url="https://example.com/jobs/real",
            ext="real",
            company="Acme",
            first_seen=_iso(NOW - timedelta(hours=1)),
        )
        _score(conn, jid, 90.0)
        n = maybe_send_urgent(
            conn,
            models=load_models(),
            profile=_profile(watchlist=[], urgent_score_min=85, urgent_daily_cap=3),
            now=NOW,
            http_post=_http(posts),
        )
        row = conn.execute(
            "SELECT notified_at, notify_kind FROM jobs WHERE id = ?", (jid,)
        ).fetchone()
    finally:
        conn.close()
    assert len(n) == 1
    assert row["notified_at"]
    assert row["notify_kind"] == "urgent"


def test_morning_guest_rows_skips_notified(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    profile = load_profile()
    models = load_models()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/g",
            ext="g",
            company="Acme",
            first_seen=_iso(NOW - timedelta(hours=1)),
        )
        _score(conn, jid, 90.0)
        before = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=profile,
            now=NOW,
        )
        stamp_notified(conn, jid, "urgent")
        after = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=profile,
            now=NOW,
        )
        sheet = relevant_unapplied_jobs(
            conn,
            models.score_model,
            models.score_prompt_version,
            profile=profile,
            now=NOW,
        )
    finally:
        conn.close()
    assert any(int(r["id"]) == jid for r in before)
    assert all(int(r["id"]) != jid for r in after)
    assert any(int(r["id"]) == jid for r in sheet)


def test_relevant_unapplied_jobs_still_sees_notified(tmp_path):
    test_morning_guest_rows_skips_notified(tmp_path)


def test_urgent_only_no_pager(monkeypatch, tmp_path, capsys):
    posts = []
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
            url="https://example.com/jobs/u",
            ext="u",
            company="Acme",
        )
        _score(conn, jid, 90.0)
    finally:
        conn.close()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        if json_mode:
            return json.dumps({"jobs": []})
        raise AssertionError("summarizer should not run")

    monkeypatch.setattr(
        "jobdigest.digest.load_profile",
        lambda: _profile(watchlist=[], urgent_score_min=85, urgent_daily_cap=3),
    )
    code = run(
        db_path=db,
        chat_fn=chat_fn,
        telegram_http_post=_http(posts),
        urgent_only=True,
    )
    _ = capsys.readouterr()
    assert code == 0
    texts = [p["json"].get("text", "") for p in posts]
    assert PAGER_TEXT not in texts
    assert any("Apply" in str(p["json"].get("reply_markup")) for p in posts)
    conn = connect(db)
    try:
        assert last_digest_job_ids(conn) == [jid]
    finally:
        conn.close()
