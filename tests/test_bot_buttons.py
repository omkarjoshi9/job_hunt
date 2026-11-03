"""Contract: every Telegram button tap has a definitive, visible end.

For each button (open, save, applied, hide, why-me, mail) and each way a tap can go
wrong, the bot must answer the tap exactly once and leave a lasting message (an edit
of the card or a new message) -- never a silent no-op.
"""

from __future__ import annotations

import json

import pytest
from test_bot import NOW, _mail_setup, _mail_update, _OkPost

import jobdigest.bot as bot_module
from jobdigest.bot import drain, handle_update, run_for

KINDS = ["o", "s", "a", "h", "w", "m"]


def _tap(kind: str, jid: int) -> dict:
    upd = _mail_update(jid)
    upd["callback_query"]["data"] = f"{kind}:{jid}"
    return upd


def _answers(posts):
    return [p for p in posts if p["url"].endswith("answerCallbackQuery")]


def _lasting(posts):
    return [
        p
        for p in posts
        if p["url"].endswith("sendMessage") or p["url"].endswith("editMessageText")
    ]


@pytest.fixture(autouse=True)
def _offline_contacts(monkeypatch):
    monkeypatch.setattr("jobdigest.contacts.enrich_job", lambda *a, **k: [])


@pytest.mark.parametrize("kind", KINDS)
def test_tap_on_existing_job_ends_visibly(kind, monkeypatch, tmp_path):
    posts, http_post, conn, jid = _mail_setup(monkeypatch, tmp_path, f"ok-{kind}")
    try:
        handle_update(_tap(kind, jid), conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    assert len(_answers(posts)) == 1, "tap must be answered exactly once"
    assert _lasting(posts), f"button {kind!r} left no visible result"


@pytest.mark.parametrize("kind", KINDS)
def test_tap_on_missing_job_explains_itself(kind, monkeypatch, tmp_path):
    posts, http_post, conn, _jid = _mail_setup(monkeypatch, tmp_path, f"miss-{kind}")
    try:
        handle_update(_tap(kind, 987654), conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    assert len(_answers(posts)) == 1
    assert _lasting(posts), f"button {kind!r} on a missing job was silent"


@pytest.mark.parametrize("kind", KINDS)
def test_tap_when_handler_crashes_reports_failure(kind, monkeypatch, tmp_path):
    posts, http_post, conn, jid = _mail_setup(monkeypatch, tmp_path, f"boom-{kind}")

    def explode(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_module, "apply_bot_status", explode)
    monkeypatch.setattr(bot_module, "_handle_why_me", explode)
    monkeypatch.setattr(bot_module, "_handle_mail", explode)
    monkeypatch.setattr(bot_module, "job_score_row", explode)
    try:
        handle_update(_tap(kind, jid), conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    assert len(_answers(posts)) == 1
    assert _lasting(posts)


@pytest.mark.parametrize("kind", KINDS)
def test_tap_when_database_is_down_reports_failure(kind, monkeypatch, tmp_path):
    posts, http_post, conn, jid = _mail_setup(monkeypatch, tmp_path, f"db-{kind}")
    conn.close()

    def down(*_a, **_k):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(bot_module, "connect", down)
    handle_update(_tap(kind, jid), now=NOW, http_post=http_post)
    assert len(_answers(posts)) == 1
    assert _lasting(posts)


def test_unknown_button_is_answered(monkeypatch, tmp_path):
    posts, http_post, conn, jid = _mail_setup(monkeypatch, tmp_path, "unk")
    upd = _mail_update(jid)
    upd["callback_query"]["data"] = "zzz"
    try:
        handle_update(upd, conn=conn, now=NOW, http_post=http_post)
    finally:
        conn.close()
    assert len(_answers(posts)) == 1


def test_status_buttons_change_the_card_and_the_database(monkeypatch, tmp_path):
    posts, http_post, conn, jid = _mail_setup(monkeypatch, tmp_path, "state")
    try:
        handle_update(_tap("a", jid), conn=conn, now=NOW, http_post=http_post)
        row = conn.execute("SELECT status FROM applications").fetchone()
    finally:
        conn.close()
    assert row["status"] == "applied"
    edits = [p for p in posts if p["url"].endswith("editMessageText")]
    assert edits and "Marked applied." in edits[-1]["json"]["text"]


class FakeTelegram:
    """Serves queued updates through getUpdates and records everything else."""

    def __init__(self, updates):
        self.updates = list(updates)
        self.posts = []
        self.get_calls = 0

    def __call__(self, url, json=None, timeout=None):  # noqa: A002
        payload = dict(json or {})
        self.posts.append({"url": url, "json": payload})
        if url.endswith("getUpdates"):
            self.get_calls += 1
            offset = int(payload.get("offset") or 0)
            batch = [u for u in self.updates if u["update_id"] >= offset]
            return _Resp({"ok": True, "result": batch})
        return _Resp({"ok": True, "result": {"message_id": 1}})


class _Resp(_OkPost):
    def __init__(self, body):
        self.text = json.dumps(body)


def _numbered(kind, jid, n):
    upd = _tap(kind, jid)
    upd["update_id"] = n
    return upd


def test_drain_handles_everything_pending_then_stops(monkeypatch, tmp_path):
    posts, _hp, conn, jid = _mail_setup(monkeypatch, tmp_path, "drain")
    conn.close()
    tg = FakeTelegram([_numbered("s", jid, 1), _numbered("a", jid, 2), _numbered("h", jid, 3)])
    handled = drain(db_path=tmp_path / "jobs.db", http_post=tg)
    assert handled >= 1
    assert len(_answers(tg.posts)) == 3
    assert tg.get_calls < 20, "drain must terminate once Telegram has nothing left"


def test_run_for_stops_at_its_deadline(monkeypatch, tmp_path):
    posts, _hp, conn, jid = _mail_setup(monkeypatch, tmp_path, "runfor")
    conn.close()
    tg = FakeTelegram([_numbered("s", jid, 1)])
    now = {"t": 0.0}

    def clock():
        return now["t"]

    def tick(_seconds):
        now["t"] += 10.0

    # each poll_once advances the fake clock so the loop must hit the deadline
    real = bot_module.poll_once

    def advancing(**kw):
        now["t"] += 10.0
        return real(**kw)

    monkeypatch.setattr(bot_module, "poll_once", advancing)
    run_for(35, db_path=tmp_path / "jobs.db", http_post=tg, clock=clock, sleep=tick)
    assert 1 <= tg.get_calls <= 5
    assert len(_answers(tg.posts)) == 1


# --- the workflow that makes the buttons work -------------------------------------


def _bot_workflow():
    import yaml
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "bot.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_bot_workflow_runs_the_poller_on_a_schedule():
    wf = _bot_workflow()
    on = wf.get("on") or wf.get(True)  # YAML parses a bare `on` key as boolean True
    assert on["schedule"] and "workflow_dispatch" in on
    steps = wf["jobs"]["bot"]["steps"]
    runs = [s.get("run", "") for s in steps]
    assert any("jobdigest.bot --max-seconds" in r for r in runs)
    assert wf["concurrency"]["cancel-in-progress"] is False  # overlap queues, never kills
    seconds = int(next(r for r in runs if "--max-seconds" in r).split("--max-seconds")[1].split()[0])
    assert seconds < wf["jobs"]["bot"]["timeout-minutes"] * 60


def test_bot_workflow_refuses_to_run_without_the_shared_database():
    wf = _bot_workflow()
    steps = wf["jobs"]["bot"]["steps"]
    guard = next(s for s in steps if "DATABASE_URL" in s.get("run", "") and "exit 1" in s["run"])
    poll = next(s for s in steps if "jobdigest.bot" in s.get("run", ""))
    assert steps.index(guard) < steps.index(poll)
    assert "secrets.DATABASE_URL" in poll["env"]["DATABASE_URL"]
    for secret in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        assert f"secrets.{secret}" in poll["env"][secret]
