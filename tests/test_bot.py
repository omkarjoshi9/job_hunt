import json

import pytest
from datetime import datetime, timezone

from dataclasses import replace

import jobdigest.bot as bot_module
from jobdigest.bot import handle_update, poll_once
from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    apply_bot_status,
    connect,
    guest_rows,
    upsert_application,
    upsert_contact,
    upsert_job,
    upsert_score,
)


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_no_contact_cache():
    bot_module._NO_CONTACT.clear()
    yield
    bot_module._NO_CONTACT.clear()


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


def _score_pass(conn, jid: int):
    models = load_models()
    upsert_score(
        conn,
        jid,
        models.score_model,
        models.score_prompt_version,
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


def _guests(conn, profile, *, digest_max: int = 12):
    models = load_models()
    return guest_rows(
        conn,
        models.score_model,
        models.score_prompt_version,
        digest_max,
        profile=profile,
        now=NOW,
    )


class _OkPost:
    status_code = 200
    text = '{"ok": true, "result": []}'

    def json(self):
        return json.loads(self.text)


def test_apply_bot_status_applied_excludes_guest_rows(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/a",
            ext="a",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        assert any(int(r["id"]) == jid for r in _guests(conn, profile))
        assert apply_bot_status(conn, jid, "applied") == "ok"
        assert all(int(r["id"]) != jid for r in _guests(conn, profile))
        row = conn.execute("SELECT status FROM applications").fetchone()
        assert row["status"] == "applied"
    finally:
        conn.close()


def test_apply_bot_status_hide_is_not_applied(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/h",
            ext="h",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        assert apply_bot_status(conn, jid, "hidden") == "ok"
        row = conn.execute("SELECT status FROM applications").fetchone()
        assert row["status"] == "hidden"
        assert row["status"] != "applied"
        assert all(int(r["id"]) != jid for r in _guests(conn, profile))
    finally:
        conn.close()


def test_apply_bot_status_save(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/s",
            ext="s",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        assert apply_bot_status(conn, jid, "saved") == "ok"
        row = conn.execute("SELECT status FROM applications").fetchone()
        assert row["status"] == "saved"
    finally:
        conn.close()


def test_apply_bot_status_preserves_oa_notes(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/oa",
            ext="oa",
            title="Software Engineer Intern",
            company="Acme",
        )
        upsert_application(
            conn,
            apply_url="https://example.com/jobs/oa",
            status="oa",
            notes="mine",
        )
        assert apply_bot_status(conn, jid, "applied") == "skip"
        row = conn.execute("SELECT status, notes FROM applications").fetchone()
        assert row["status"] == "oa"
        assert row["notes"] == "mine"
    finally:
        conn.close()


def test_apply_bot_status_restamps_applied_on_when_entering_applied(
    monkeypatch, tmp_path
):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/r",
            ext="r",
            title="Software Engineer Intern",
            company="Acme",
        )
        assert apply_bot_status(conn, jid, "saved") == "ok"
        first = conn.execute("SELECT applied_on FROM applications").fetchone()[
            "applied_on"
        ]
        monkeypatch.setattr("jobdigest.db.utc_now", lambda: "2099-01-01T00:00:00Z")
        assert apply_bot_status(conn, jid, "applied") == "ok"
        second = conn.execute("SELECT applied_on, status FROM applications").fetchone()
        assert second["status"] == "applied"
        assert second["applied_on"] == "2099-01-01T00:00:00Z"
        assert second["applied_on"] != first
    finally:
        conn.close()


def test_apply_bot_status_does_not_restamp_if_already_applied(monkeypatch, tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/rr",
            ext="rr",
            title="Software Engineer Intern",
            company="Acme",
        )
        assert apply_bot_status(conn, jid, "applied") == "ok"
        first = conn.execute("SELECT applied_on FROM applications").fetchone()[
            "applied_on"
        ]
        monkeypatch.setattr("jobdigest.db.utc_now", lambda: "2099-01-01T00:00:00Z")
        assert apply_bot_status(conn, jid, "applied") == "ok"
        second = conn.execute("SELECT applied_on FROM applications").fetchone()[
            "applied_on"
        ]
        assert second == first
    finally:
        conn.close()


def test_handle_update_open_sends_new_action_card(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/open",
            ext="open",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        handle_update(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "cb1",
                    "data": f"o:{jid}",
                    "message": {
                        "message_id": 9,
                        "chat": {"id": 7},
                        "text": "Tap a number to open a card.",
                    },
                },
            },
            conn=conn,
            now=NOW,
            http_post=http_post,
        )
    finally:
        conn.close()
    methods = [p["url"].rsplit("/", 1)[-1] for p in posts]
    assert "answerCallbackQuery" in methods
    assert "sendMessage" in methods
    assert "editMessageText" not in methods
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    kb = send["json"]["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["url"] == "https://example.com/jobs/open"
    assert "Acme" in send["json"]["text"]
    assert "Why you:" in send["json"]["text"]


def test_handle_update_applied_edits_action_card(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/ap",
            ext="ap",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        handle_update(
            {
                "update_id": 2,
                "callback_query": {
                    "id": "cb2",
                    "data": f"a:{jid}",
                    "message": {
                        "message_id": 11,
                        "chat": {"id": 7},
                        "text": "card",
                    },
                },
            },
            conn=conn,
            now=NOW,
            http_post=http_post,
        )
        row = conn.execute("SELECT status FROM applications").fetchone()
        assert row["status"] == "applied"
        assert all(int(r["id"]) != jid for r in _guests(conn, profile))
    finally:
        conn.close()
    methods = [p["url"].rsplit("/", 1)[-1] for p in posts]
    assert methods[0] == "answerCallbackQuery"
    assert "editMessageText" in methods
    edit = next(p for p in posts if p["url"].endswith("editMessageText"))
    assert "Marked applied." in edit["json"]["text"]
    kb = edit["json"]["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["url"] == "https://example.com/jobs/ap"
    callbacks = [
        btn.get("callback_data")
        for row in kb
        for btn in row
        if "callback_data" in btn
    ]
    assert callbacks == [f"w:{jid}", f"m:{jid}"]
    assert all(
        not (btn.get("callback_data") or "").startswith(("s:", "a:", "h:"))
        for row in kb
        for btn in row
    )


def test_handle_update_wrong_chat_silent(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/w",
            ext="w",
            title="Software Engineer Intern",
            company="Acme",
        )
        handle_update(
            {
                "update_id": 3,
                "callback_query": {
                    "id": "cb3",
                    "data": f"a:{jid}",
                    "message": {
                        "message_id": 12,
                        "chat": {"id": 8},
                        "text": "card",
                    },
                },
            },
            conn=conn,
            now=NOW,
            http_post=http_post,
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        assert count == 0
    finally:
        conn.close()
    methods = [p["url"].rsplit("/", 1)[-1] for p in posts]
    assert methods == ["answerCallbackQuery"]


def test_handle_update_ignores_slash_commands(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        posts.append(1)
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    handle_update(
        {
            "update_id": 4,
            "message": {"chat": {"id": 7}, "text": "/start"},
        },
        db_path=tmp_path / "jobs.db",
        now=NOW,
        http_post=http_post,
    )
    assert posts == []


def test_handle_update_unknown_slash_ignored(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        posts.append(1)
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    handle_update(
        {
            "update_id": 5,
            "message": {"chat": {"id": 7}, "text": "/foo"},
        },
        db_path=tmp_path / "jobs.db",
        now=NOW,
        http_post=http_post,
    )
    assert posts == []


def test_handle_update_health_replies(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    handle_update(
        {
            "update_id": 50,
            "message": {"chat": {"id": 7}, "text": "/health"},
        },
        db_path=tmp_path / "jobs.db",
        now=NOW,
        http_post=http_post,
    )
    assert posts
    assert posts[0]["json"]["text"] == "All sources healthy."
    assert "parse_mode" not in posts[0]["json"]


def test_handle_update_followups_replies(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        upsert_application(
            conn,
            apply_url="https://example.com/jobs/fu",
            company="Acme",
            title="Intern",
            status="applied",
        )
        conn.execute(
            "UPDATE applications SET applied_on = ?",
            ("2026-08-31T00:30:00Z",),
        )
        conn.commit()
        handle_update(
            {
                "update_id": 6,
                "message": {"chat": {"id": 7}, "text": "/followups"},
            },
            conn=conn,
            now=datetime(2026, 9, 9, 6, 30, tzinfo=timezone.utc),
            http_post=http_post,
        )
    finally:
        conn.close()
    assert posts[0]["json"]["text"].startswith("Acme — Intern")
    assert "https://example.com/jobs/fu" in posts[0]["json"]["text"]
    assert "parse_mode" not in posts[0]["json"]


def test_handle_update_followup_alias(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    handle_update(
        {
            "update_id": 7,
            "message": {"chat": {"id": 7}, "text": "/followups@SomeBot"},
        },
        db_path=tmp_path / "jobs.db",
        now=datetime(2026, 9, 9, 6, 30, tzinfo=timezone.utc),
        http_post=http_post,
    )
    assert posts[0]["json"]["text"] == "No follow-ups due."


def test_handle_update_followups_empty_still_replies(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    handle_update(
        {
            "update_id": 8,
            "message": {"chat": {"id": 7}, "text": "/followup"},
        },
        db_path=tmp_path / "jobs.db",
        now=datetime(2026, 9, 9, 6, 30, tzinfo=timezone.utc),
        http_post=http_post,
    )
    assert posts[0]["json"]["text"] == "No follow-ups due."


def test_handle_update_followups_wrong_chat_silent(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        posts.append(1)
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    handle_update(
        {
            "update_id": 9,
            "message": {"chat": {"id": 8}, "text": "/followups"},
        },
        db_path=tmp_path / "jobs.db",
        now=datetime(2026, 9, 9, 6, 30, tzinfo=timezone.utc),
        http_post=http_post,
    )
    assert posts == []


def test_poll_once_posts_getUpdates(monkeypatch):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    assert poll_once(offset=3, http_post=http_post) == 3
    assert posts[0]["url"] == "https://api.telegram.org/bottok/getUpdates"
    assert posts[0]["json"]["offset"] == 3
    assert posts[0]["json"]["allowed_updates"] == ["message", "callback_query"]


def _why_update(jid: int, *, chat_id: int = 7):
    return {
        "update_id": 90,
        "callback_query": {
            "id": "cbw",
            "data": f"w:{jid}",
            "message": {
                "message_id": 21,
                "chat": {"id": chat_id},
                "text": "card",
            },
        },
    }


def _why_chat_fn(profile):
    calls = {"n": 0}

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, client
        assert json_mode is False
        calls["n"] += 1
        cite = profile.internships[0]
        body = " ".join(["experience"] * 90)
        return f"CITE: {cite}\n\n{body}"

    chat_fn.calls = calls
    return chat_fn


def _patch_blurb_models(monkeypatch, *, enabled: bool = True):
    m = load_models()
    if enabled:
        m = replace(
            m,
            blurb_model="anthropic/claude-sonnet-4",
            blurb_prompt_version="v1",
        )
    else:
        m = replace(m, blurb_model=None, blurb_prompt_version=None)
    monkeypatch.setattr("jobdigest.bot.load_models", lambda: m)
    return m


def test_handle_update_why_me_sends_new_message(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    _patch_blurb_models(monkeypatch)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    chat_fn = _why_chat_fn(profile)
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/why-ok",
            ext="why-ok",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        handle_update(
            _why_update(jid),
            conn=conn,
            now=NOW,
            http_post=http_post,
            chat_fn=chat_fn,
        )
    finally:
        conn.close()
    methods = [p["url"].rsplit("/", 1)[-1] for p in posts]
    assert methods[0] == "answerCallbackQuery"
    assert "sendMessage" in methods
    assert "editMessageText" not in methods
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    assert "experience" in send["json"]["text"]
    assert "parse_mode" not in send["json"]
    assert chat_fn.calls["n"] == 1


def test_handle_update_why_me_refuses_reject(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, json_mode, client
        raise AssertionError("chat_fn must not be called")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    _patch_blurb_models(monkeypatch)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/why-rej",
            ext="why-rej",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        conn.execute(
            "UPDATE jobs SET hard_status = 'reject', hard_reason = 'yoe' WHERE id = ?",
            (jid,),
        )
        conn.commit()
        handle_update(
            _why_update(jid),
            conn=conn,
            now=NOW,
            http_post=http_post,
            chat_fn=chat_fn,
        )
    finally:
        conn.close()
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    assert send["json"]["text"] == "Can't draft: this job failed hard filters."


def test_handle_update_why_me_refuses_inactive(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, json_mode, client
        raise AssertionError("chat_fn must not be called")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    _patch_blurb_models(monkeypatch)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/why-inact",
            ext="why-inact",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        conn.execute("UPDATE jobs SET is_active = 0 WHERE id = ?", (jid,))
        conn.commit()
        handle_update(
            _why_update(jid),
            conn=conn,
            now=NOW,
            http_post=http_post,
            chat_fn=chat_fn,
        )
    finally:
        conn.close()
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    assert send["json"]["text"] == "Can't draft: job is no longer active."


def test_handle_update_why_me_refuses_hidden(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, json_mode, client
        raise AssertionError("chat_fn must not be called")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    _patch_blurb_models(monkeypatch)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/why-hid",
            ext="why-hid",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        assert apply_bot_status(conn, jid, "hidden") == "ok"
        handle_update(
            _why_update(jid),
            conn=conn,
            now=NOW,
            http_post=http_post,
            chat_fn=chat_fn,
        )
    finally:
        conn.close()
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    assert send["json"]["text"] == "Can't draft: job is hidden."


def test_handle_update_why_me_allows_saved(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    _patch_blurb_models(monkeypatch)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    chat_fn = _why_chat_fn(profile)
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/why-saved",
            ext="why-saved",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        assert apply_bot_status(conn, jid, "saved") == "ok"
        handle_update(
            _why_update(jid),
            conn=conn,
            now=NOW,
            http_post=http_post,
            chat_fn=chat_fn,
        )
    finally:
        conn.close()
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    assert "experience" in send["json"]["text"]
    assert chat_fn.calls["n"] == 1


def test_handle_update_why_me_wrong_chat_silent(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, json_mode, client
        raise AssertionError("chat_fn must not be called")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    _patch_blurb_models(monkeypatch)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/why-wrong",
            ext="why-wrong",
            title="Software Engineer Intern",
            company="Acme",
        )
        handle_update(
            _why_update(jid, chat_id=8),
            conn=conn,
            now=NOW,
            http_post=http_post,
            chat_fn=chat_fn,
        )
    finally:
        conn.close()
    methods = [p["url"].rsplit("/", 1)[-1] for p in posts]
    assert methods == ["answerCallbackQuery"]


def test_handle_update_why_me_does_not_call_chat_on_refuse(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    calls = {"n": 0}

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, json_mode, client
        calls["n"] += 1
        raise AssertionError("chat_fn must not be called")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    _patch_blurb_models(monkeypatch)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/why-nochat",
            ext="why-nochat",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        conn.execute(
            "UPDATE jobs SET hard_status = 'reject' WHERE id = ?",
            (jid,),
        )
        conn.commit()
        handle_update(
            _why_update(jid),
            conn=conn,
            now=NOW,
            http_post=http_post,
            chat_fn=chat_fn,
        )
    finally:
        conn.close()
    assert calls["n"] == 0
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    assert send["json"]["text"].startswith("Can't draft:")


def test_handle_update_why_me_missing_blurb_config_refuses(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, json_mode, client
        raise AssertionError("chat_fn must not be called")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    _patch_blurb_models(monkeypatch, enabled=False)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/why-cfg",
            ext="why-cfg",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        handle_update(
            _why_update(jid),
            conn=conn,
            now=NOW,
            http_post=http_post,
            chat_fn=chat_fn,
        )
    finally:
        conn.close()
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    assert send["json"]["text"] == "Can't draft: blurb model not configured."


def _mail_update(jid: int, *, chat_id: int = 7):
    return {
        "update_id": 91,
        "callback_query": {
            "id": "cbm",
            "data": f"m:{jid}",
            "message": {
                "message_id": 22,
                "chat": {"id": chat_id},
                "text": "card",
            },
        },
    }


def test_handle_update_mail_sends_draft(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/mail-ok",
            ext="mail-ok",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        upsert_contact(
            conn,
            job_id=jid,
            company="Acme",
            name="Priya Sharma",
            source="jd",
            email="priya.sharma@acme.com",
            role="Hiring Manager",
            confidence=100,
        )
        handle_update(_mail_update(jid), conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    methods = [p["url"].rsplit("/", 1)[-1] for p in posts]
    assert methods[0] == "answerCallbackQuery"
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    text = send["json"]["text"]
    assert "Subject:" in text
    assert "To: priya.sharma@acme.com" in text
    assert "Send manually — no SMTP" in text
    assert "mailto:priya.sharma@acme.com" in text


def test_handle_update_mail_none_found(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    monkeypatch.setattr("jobdigest.contacts.enrich_job", lambda *a, **k: [])
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/mail-none",
            ext="mail-none",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        handle_update(_mail_update(jid), conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    assert send["json"]["text"] == "No hiring manager found for this job."


def test_handle_update_mail_wrong_chat_silent(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/mail-wrong",
            ext="mail-wrong",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        handle_update(
            _mail_update(jid, chat_id=99),
            conn=conn,
            now=NOW,
            http_post=http_post,
        )
    finally:
        conn.close()
    methods = [p["url"].rsplit("/", 1)[-1] for p in posts]
    assert methods == ["answerCallbackQuery"]


def test_handle_update_mail_refuses_hidden(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    try:
        jid = _pass_job(
            conn,
            profile,
            url="https://example.com/jobs/mail-hid",
            ext="mail-hid",
            title="Software Engineer Intern",
            company="Acme",
        )
        _score_pass(conn, jid)
        apply_bot_status(conn, jid, "hidden")
        handle_update(_mail_update(jid), conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    send = next(p for p in posts if p["url"].endswith("sendMessage"))
    assert send["json"]["text"] == "Can't draft: job is hidden."


def _mail_setup(monkeypatch, tmp_path, ext):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    conn = connect(tmp_path / "jobs.db")
    jid = _pass_job(
        conn,
        load_profile(),
        url=f"https://example.com/jobs/{ext}",
        ext=ext,
        title="Software Engineer Intern",
        company="Acme",
    )
    _score_pass(conn, jid)
    return posts, http_post, conn, jid


def _sent_texts(posts):
    return [p["json"]["text"] for p in posts if p["url"].endswith("sendMessage")]


def test_mail_negative_result_cached_no_rerun(monkeypatch, tmp_path):
    posts, http_post, conn, jid = _mail_setup(monkeypatch, tmp_path, "mail-neg")
    calls = []
    monkeypatch.setattr(
        "jobdigest.contacts.enrich_job", lambda *a, **k: calls.append(1) or []
    )
    try:
        for _ in range(3):
            handle_update(_mail_update(jid), conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    assert len(calls) == 1
    assert _sent_texts(posts) == ["No hiring manager found for this job."] * 3


def test_mail_lookup_failure_tells_user_and_is_not_cached(monkeypatch, tmp_path):
    posts, http_post, conn, jid = _mail_setup(monkeypatch, tmp_path, "mail-fail")
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("network")

    monkeypatch.setattr("jobdigest.contacts.enrich_job", boom)
    try:
        handle_update(_mail_update(jid), conn=conn, now=NOW, http_post=http_post)
        handle_update(_mail_update(jid), conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    assert len(calls) == 2
    assert _sent_texts(posts) == [
        "Can't draft: contact lookup failed. Try again later."
    ] * 2


def test_mail_negative_cache_expires(monkeypatch, tmp_path):
    posts, http_post, conn, jid = _mail_setup(monkeypatch, tmp_path, "mail-ttl")
    calls = []
    monkeypatch.setattr(
        "jobdigest.contacts.enrich_job", lambda *a, **k: calls.append(1) or []
    )
    try:
        handle_update(_mail_update(jid), conn=conn, now=NOW, http_post=http_post)
        for key in list(bot_module._NO_CONTACT):
            bot_module._NO_CONTACT[key] = 0.0
        handle_update(_mail_update(jid), conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    assert len(calls) == 2


def test_unknown_callback_answered_with_text(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    upd = _mail_update(1)
    upd["callback_query"]["data"] = "zz:1"
    conn = connect(tmp_path / "jobs.db")
    try:
        handle_update(upd, conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    answers = [p for p in posts if p["url"].endswith("answerCallbackQuery")]
    assert len(answers) == 1 and answers[0]["json"]["text"] == "Unknown action."


def test_open_callback_missing_job_answers_message(monkeypatch, tmp_path):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    upd = _mail_update(9999)
    upd["callback_query"]["data"] = "o:9999"
    conn = connect(tmp_path / "jobs.db")
    try:
        handle_update(upd, conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    answers = [p for p in posts if p["url"].endswith("answerCallbackQuery")]
    assert len(answers) == 1 and answers[0]["json"]["text"] == "Job not found."
    assert not any(p["url"].endswith("sendMessage") for p in posts)


def test_slow_action_exception_sends_message(monkeypatch, tmp_path):
    posts, http_post, conn, jid = _mail_setup(monkeypatch, tmp_path, "mail-exc")

    def explode(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_module, "_handle_mail", explode)
    try:
        handle_update(_mail_update(jid), conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    assert _sent_texts(posts) == ["Something went wrong. Try again later."]
