from __future__ import annotations

import io
import json
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path

from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    collect_is_ok,
    connect,
    finish_run,
    guest_rows,
    insert_run,
)
from jobdigest.envfile import load_dotenv
from jobdigest.logutil import configure, get_logger
from jobdigest.openrouter import chat as openrouter_chat
from jobdigest.score import run as score_run
from jobdigest.summarize import summarize_digest
from jobdigest.telegram import deliver as telegram_deliver
from jobdigest.telegram import split_message

ChatFn = Callable[..., str]
TelegramHttpPost = Callable[..., object]
log = get_logger("digest")


def _digest_url(apply_url: str | None) -> str:
    url = apply_url or ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return ""


def _why(reasons_json: str | None) -> str:
    if not reasons_json:
        return ""
    try:
        data = json.loads(reasons_json)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, dict):
        return str(data.get("why") or "")[:160]
    return ""


def _guest_dicts(rows) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "id": int(row["id"]),
                "company": row["company"] or "",
                "title": row["title"] or "",
                "location": row["location_raw"] or "",
                "url": _digest_url(row["apply_url"]),
                "score": float(row["score"] or 0),
                "why": _why(row["reasons_json"]),
            }
        )
    return out


def _split_message(text: str, limit: int = 3800) -> list[str]:
    return split_message(text, limit=limit)


def _print_digest(text: str) -> None:
    parts = split_message(text)
    n = len(parts)
    if n == 1:
        print(parts[0])
        return
    for i, part in enumerate(parts, 1):
        print(f"{i}/{n}")
        print(part)
        if i < n:
            print()


def _default_chat(
    messages: list[dict[str, str]],
    *,
    model: str,
    api_key: str,
    json_mode: bool = False,
    client=None,
) -> str:
    return openrouter_chat(
        messages, model=model, api_key=api_key, json_mode=json_mode, client=client
    )


def _loud_fail(
    conn,
    run_id: int,
    message: str,
    *,
    telegram_http_post: TelegramHttpPost | None = None,
) -> int:
    finish_run(conn, run_id, "error", {}, error=message)
    print(message)
    print(message, file=sys.stderr)
    telegram_deliver(message, required=True, http_post=telegram_http_post)
    return 1


def run(
    *,
    db_path: Path | None = None,
    chat_fn: ChatFn | None = None,
    score_chat_fn: ChatFn | None = None,
    summarize_chat_fn: ChatFn | None = None,
    telegram_http_post: TelegramHttpPost | None = None,
) -> int:
    load_dotenv()
    configure()
    score_fn = score_chat_fn or chat_fn
    sum_fn = summarize_chat_fn or chat_fn
    if sum_fn is None:
        sum_fn = _default_chat

    models = load_models()
    profile = load_profile()
    conn = connect(db_path)
    run_id = insert_run(conn, "digest")
    try:
        collect_ok = collect_is_ok(conn)
        log.info("digest collect_is_ok=%s", collect_ok)
        if not collect_ok:
            return _loud_fail(
                conn,
                run_id,
                "COLLECT FAILED",
                telegram_http_post=telegram_http_post,
            )

        buf = io.StringIO()
        with redirect_stdout(buf):
            score_code = score_run(db_path=db_path, chat_fn=score_fn)
        if score_code != 0:
            return _loud_fail(
                conn,
                run_id,
                "SCORE FAILED",
                telegram_http_post=telegram_http_post,
            )

        conn.close()
        conn = connect(db_path)
        rows = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            profile.digest_max,
        )
        guest = _guest_dicts(rows)
        titles = [f"{g['company']}: {g['title']}" for g in guest]
        log.info("digest guests=%s titles=%s", len(guest), titles)
        if not guest:
            log.info("digest empty skip send")
            finish_run(conn, run_id, "ok", {"guest": 0, "path": "empty"})
            return 0

        api_key = os.environ.get("OPENROUTER_API_KEY") or ""
        text, path = summarize_digest(
            guest,
            chat_fn=sum_fn,
            model=models.summarize_model,
            api_key=api_key,
        )
        _print_digest(text)
        finish_run(conn, run_id, "ok", {"guest": len(guest), "path": path})
        sent = telegram_deliver(
            text, required=True, http_post=telegram_http_post
        )
        log.info("digest telegram_ok=%s", sent)
        if not sent:
            return 1
        return 0
    finally:
        conn.close()
