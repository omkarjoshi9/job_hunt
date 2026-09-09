from __future__ import annotations

import argparse
import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobdigest.blurb import draft_blurb, ground
from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    apply_bot_status,
    best_contact_for_job,
    connect,
    contacts_for_job,
    job_score_row,
    upsert_contact,
)
from jobdigest.digest import _guest_dicts
from jobdigest.email_draft import draft_email, format_telegram
from jobdigest.envfile import load_dotenv
from jobdigest.logutil import configure, get_logger
from jobdigest.followups import due_followups, format_followups
from jobdigest.health import collect_health, format_health
from jobdigest.openrouter import OpenRouterError, chat as openrouter_chat
from jobdigest.summarize import render_card
from jobdigest.telegram import (
    ALLOWED_UPDATES,
    TelegramError,
    action_markup,
    answer_callback_query,
    credentials,
    deliver,
    edit_message_text,
    get_updates,
    send_action_card,
    send_blurb,
)

log = get_logger("bot")

HttpPost = Callable[..., Any]
ChatFn = Callable[..., str]

_FOLLOWUP_CMDS = frozenset({"/followups", "/followup"})
_HEALTH_CMDS = frozenset({"/health"})
_CB = re.compile(r"^(o|s|a|h|w|m):(\d+)$")
_CB_STATUS = {"s": "saved", "a": "applied", "h": "hidden"}
_STATUS_LINE = {
    "saved": "Saved.",
    "applied": "Marked applied.",
    "hidden": "Hidden.",
}


def _slash_command(text: str) -> str:
    token = text.split()[0] if text else ""
    return token.split("@", 1)[0].casefold()


def _update_chat_id(update: dict) -> str:
    cq = update.get("callback_query")
    if isinstance(cq, dict):
        msg = cq.get("message")
        if isinstance(msg, dict):
            chat = msg.get("chat")
            if isinstance(chat, dict) and chat.get("id") is not None:
                return str(chat["id"])
        frm = cq.get("from")
        if isinstance(frm, dict) and frm.get("id") is not None:
            return str(frm["id"])
    msg = update.get("message")
    if isinstance(msg, dict):
        chat = msg.get("chat")
        if isinstance(chat, dict) and chat.get("id") is not None:
            return str(chat["id"])
    return ""


def _send_plain(text: str, *, http_post: HttpPost | None) -> None:
    token, chat_id = credentials()
    if not token or not chat_id:
        return
    try:
        send_blurb(text, token=token, chat_id=chat_id, http_post=http_post)
    except TelegramError:
        log.error("telegram blurb send fail")


def _application_status(conn: Any, job_row: Any) -> str | None:
    try:
        norm = str(job_row["apply_url_norm"] or "")
    except (KeyError, IndexError, TypeError):
        return None
    if not norm:
        return None
    row = conn.execute(
        "SELECT status FROM applications WHERE apply_url_norm = ?",
        (norm,),
    ).fetchone()
    if row is None:
        return None
    return str(row["status"] or "").strip().lower() or None


def _why_me_gate(row: Any, conn: Any, profile: Any, models: Any) -> str | None:
    if row is None:
        return "Can't draft: job not found."
    if str(row["hard_status"] or "") != "pass":
        return "Can't draft: this job failed hard filters."
    if int(row["is_active"] or 0) != 1:
        return "Can't draft: job is no longer active."
    if row["canonical_job_id"] is not None:
        return "Can't draft: open the canonical posting."
    if _application_status(conn, row) == "hidden":
        return "Can't draft: job is hidden."
    internships = [str(x).strip() for x in (profile.internships or []) if str(x).strip()]
    if not internships:
        return "Can't draft: no internships in profile to cite."
    if not models.blurb_model or not models.blurb_prompt_version:
        return "Can't draft: blurb model not configured."
    return None


def _handle_why_me(
    job_id: int,
    *,
    conn: Any,
    http_post: HttpPost | None,
    chat_fn: ChatFn | None,
) -> None:
    models = load_models()
    profile = load_profile()
    row = job_score_row(conn, job_id, models.score_model, models.score_prompt_version)
    refuse = _why_me_gate(row, conn, profile, models)
    if refuse:
        _send_plain(refuse, http_post=http_post)
        return
    fn = chat_fn
    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if fn is None:
        if not api_key:
            _send_plain("Can't draft: missing OPENROUTER_API_KEY.", http_post=http_post)
            return
        fn = openrouter_chat
    try:
        text, path = draft_blurb(
            profile=profile,
            job_row=row,
            chat_fn=fn,
            model=str(models.blurb_model),
            api_key=api_key,
            prompt_version=str(models.blurb_prompt_version),
        )
    except OpenRouterError:
        _send_plain("Can't draft: model error.", http_post=http_post)
        return
    if path == "refuse":
        _send_plain(text, http_post=http_post)
        return
    body, flags = ground(text, profile, row)
    if flags:
        body = f"{body}\n\nUngrounded: {', '.join(flags)}"
    _send_plain(body, http_post=http_post)


def _mail_gate(row: Any, conn: Any) -> str | None:
    if row is None:
        return "Can't draft: job not found."
    if str(row["hard_status"] or "") != "pass":
        return "Can't draft: this job failed hard filters."
    if int(row["is_active"] or 0) != 1:
        return "Can't draft: job is no longer active."
    if row["canonical_job_id"] is not None:
        return "Can't draft: open the canonical posting."
    if _application_status(conn, row) == "hidden":
        return "Can't draft: job is hidden."
    return None


def _handle_mail(
    job_id: int,
    *,
    conn: Any,
    http_post: HttpPost | None,
    chat_fn: ChatFn | None,
) -> None:
    models = load_models()
    profile = load_profile()
    row = job_score_row(conn, job_id, models.score_model, models.score_prompt_version)
    refuse = _mail_gate(row, conn)
    if refuse:
        _send_plain(refuse, http_post=http_post)
        return
    rows = contacts_for_job(conn, job_id)
    if not rows:
        from jobdigest.contacts import enrich_job

        try:
            found = enrich_job(row)
        except Exception:
            found = []
        for person in found:
            upsert_contact(
                conn,
                job_id=job_id,
                company=person.company or str(row["company"] or ""),
                name=person.name,
                source=person.source,
                email=person.email,
                role=person.role,
                domain=person.domain,
                confidence=person.confidence,
            )
    contact = best_contact_for_job(conn, job_id)
    if contact is None:
        _send_plain("No hiring manager found for this job.", http_post=http_post)
        return
    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    fn = chat_fn
    model = models.blurb_model
    if fn is None and model and api_key:
        fn = openrouter_chat
    try:
        draft = draft_email(
            profile=profile,
            job_row=row,
            contact=contact,
            chat_fn=fn,
            model=model,
            api_key=api_key,
        )
    except Exception:
        _send_plain("Can't draft: email template missing.", http_post=http_post)
        return
    _send_plain(format_telegram(draft), http_post=http_post)


def _answer(cq: dict, *, http_post: HttpPost | None) -> None:
    token, _ = credentials()
    cq_id = str(cq.get("id") or "")
    if not token or not cq_id:
        return
    try:
        answer_callback_query(cq_id, token=token, http_post=http_post)
    except TelegramError:
        log.error("telegram answer callback fail")


def _handle_callback(
    cq: dict,
    *,
    conn: Any,
    now: datetime,
    http_post: HttpPost | None,
    chat_fn: ChatFn | None = None,
) -> None:
    _answer(cq, http_post=http_post)
    raw = str(cq.get("data") or "")
    match = _CB.match(raw)
    if not match:
        return
    kind, job_s = match.group(1), match.group(2)
    job_id = int(job_s)
    if kind == "w":
        _handle_why_me(job_id, conn=conn, http_post=http_post, chat_fn=chat_fn)
        return
    if kind == "m":
        _handle_mail(job_id, conn=conn, http_post=http_post, chat_fn=chat_fn)
        return
    models = load_models()
    row = job_score_row(conn, job_id, models.score_model, models.score_prompt_version)
    if row is None:
        return
    guest = _guest_dicts([row], now=now)
    if not guest:
        return
    card = render_card(guest[0], 1, now=now)
    apply_url = str(guest[0].get("url") or "")
    if kind == "o":
        send_action_card(
            card,
            job_id=job_id,
            apply_url=apply_url,
            required=True,
            http_post=http_post,
            why_me=True,
            status_buttons=True,
        )
        return
    status = _CB_STATUS[kind]
    result = apply_bot_status(conn, job_id, status)
    if result == "ok":
        line = _STATUS_LINE[status]
        show = status != "hidden"
        markup = action_markup(
            job_id,
            apply_url,
            status_buttons=False,
            why_me=show,
            mail=show,
        )
    elif result == "skip":
        line = "Already tracking."
        markup = action_markup(
            job_id, apply_url, status_buttons=True, why_me=True
        )
    else:
        line = "Job not found."
        markup = action_markup(
            job_id, apply_url, status_buttons=True, why_me=True
        )
    token, chat_id = credentials()
    msg = cq.get("message") if isinstance(cq.get("message"), dict) else {}
    message_id = msg.get("message_id") if isinstance(msg, dict) else None
    if not token or message_id is None:
        return
    body = f"{card}\n\n{line}"
    try:
        edit_message_text(
            chat_id,
            int(message_id),
            body,
            token=token,
            http_post=http_post,
            reply_markup=markup if markup.get("inline_keyboard") else None,
        )
    except TelegramError:
        log.error("telegram edit fail")


def handle_update(
    update: dict,
    *,
    db_path: Path | None = None,
    conn: Any = None,
    now: datetime | None = None,
    http_post: HttpPost | None = None,
    chat_fn: ChatFn | None = None,
) -> None:
    if not isinstance(update, dict):
        return
    allow = credentials()[1]
    chat_id = _update_chat_id(update)
    cq = update.get("callback_query") if isinstance(update.get("callback_query"), dict) else None
    if not allow or chat_id != allow:
        if cq is not None:
            _answer(cq, http_post=http_post)
        return

    when = now or datetime.now(timezone.utc)
    own = conn is None
    if conn is None:
        conn = connect(db_path)
    try:
        if cq is not None:
            _handle_callback(
                cq, conn=conn, now=when, http_post=http_post, chat_fn=chat_fn
            )
            return
        msg = update.get("message")
        if isinstance(msg, dict):
            text = str(msg.get("text") or "").strip()
            if text.startswith("/"):
                cmd = _slash_command(text)
                if cmd in _FOLLOWUP_CMDS:
                    rows = due_followups(conn, now=when)
                    body = format_followups(rows, now=when)
                    deliver(body, required=True, http_post=http_post)
                elif cmd in _HEALTH_CMDS:
                    body = format_health(collect_health(conn))
                    deliver(body, required=True, http_post=http_post)
                return
    finally:
        if own:
            conn.close()


def poll_once(
    *,
    offset: int = 0,
    db_path: Path | None = None,
    http_post: HttpPost | None = None,
    timeout: int = 25,
) -> int:
    token, _ = credentials()
    if not token:
        return offset
    updates = get_updates(
        token=token,
        offset=offset,
        timeout=timeout,
        http_post=http_post,
        allowed_updates=list(ALLOWED_UPDATES),
    )
    last = offset
    for upd in updates:
        if not isinstance(upd, dict):
            continue
        handle_update(upd, db_path=db_path, http_post=http_post)
        uid = upd.get("update_id")
        if uid is not None:
            last = int(uid) + 1
    return last


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    configure()
    parser = argparse.ArgumentParser(prog="python -m jobdigest.bot")
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite path (default DATABASE_PATH or data/jobs.db)",
    )
    args = parser.parse_args(argv)
    db_path = Path(args.db) if args.db else None
    offset = 0
    log.info("bot poller start")
    while True:
        try:
            offset = poll_once(offset=offset, db_path=db_path)
        except KeyboardInterrupt:
            return 0
        except TelegramError:
            time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
