from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any

import httpx

from jobdigest.logutil import get_logger

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"
SPLIT_LIMIT = 3800
ALLOWED_UPDATES = ["message", "callback_query"]
PAGER_TEXT = "Tap a number to open a card."

HttpPost = Callable[..., Any]
log = get_logger("telegram")


class TelegramError(Exception):
    pass


def split_message(text: str, limit: int = SPLIT_LIMIT) -> list[str]:
    stripped = text.strip("\n")
    if len(stripped) <= limit:
        return [stripped]
    blocks = stripped.split("\n\n")
    parts: list[str] = []
    buf = ""
    for block in blocks:
        candidate = block if not buf else f"{buf}\n\n{block}"
        if len(candidate) <= limit:
            buf = candidate
            continue
        if buf:
            parts.append(buf)
            buf = ""
        if len(block) <= limit:
            buf = block
            continue
        start = 0
        while start < len(block):
            parts.append(block[start : start + limit])
            start += limit
    if buf:
        parts.append(buf)
    return parts or [stripped[:limit]]


def credentials() -> tuple[str, str]:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    return token, chat_id


def would_post(http_post: HttpPost | None = None) -> bool:
    """True when credentials exist. ``http_post`` is accepted for call-site symmetry."""
    token, chat_id = credentials()
    return bool(token and chat_id)


def require_telegram() -> bool:
    raw = (os.environ.get("JOBDIGEST_REQUIRE_TELEGRAM") or "").strip().lower()
    return raw in {"1", "true", "yes"}


def _httpx_post(url: str, json: dict | None = None, timeout: float = 30.0) -> httpx.Response:
    with httpx.Client(timeout=timeout) as client:
        return client.post(url, json=json, timeout=timeout)


def _response_json(resp: Any) -> dict:
    raw_json = getattr(resp, "json", None)
    if callable(raw_json):
        try:
            data = raw_json()
            if isinstance(data, dict):
                return data
        except ValueError:
            log.debug("telegram response json() failed; falling back to text")
    text = str(getattr(resp, "text", "") or "")
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def bot_post(
    method: str,
    payload: dict[str, Any],
    *,
    token: str,
    http_post: HttpPost | None = None,
    timeout: float = 30.0,
) -> dict:
    url = TELEGRAM_API_URL.format(token=token, method=method)
    post = http_post if http_post is not None else _httpx_post
    try:
        resp = post(url, json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        # httpx messages can embed the request URL, which contains the bot token.
        log.error("telegram send fail transport")
        raise TelegramError(f"transport error: {type(exc).__name__}") from None
    except Exception as exc:
        log.error("telegram send fail")
        raise TelegramError(f"send error: {type(exc).__name__}") from None
    status = int(getattr(resp, "status_code", 0) or 0)
    if status != 200:
        body = str(getattr(resp, "text", "") or "")[:500]
        log.error("telegram send fail http_status=%s", status)
        raise TelegramError(f"HTTP {status}: {body}")
    log.info("telegram send ok method=%s http_status=%s", method, status)
    return _response_json(resp)


def send_message(
    text: str,
    *,
    token: str,
    chat_id: str,
    http_post: HttpPost | None = None,
    timeout: float = 30.0,
    reply_markup: dict | None = None,
) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    bot_post(
        "sendMessage",
        payload,
        token=token,
        http_post=http_post,
        timeout=timeout,
    )


def get_updates(
    *,
    token: str,
    offset: int = 0,
    timeout: int = 25,
    http_post: HttpPost | None = None,
    allowed_updates: list[str] | None = None,
) -> list:
    payload = {
        "offset": int(offset),
        "timeout": int(timeout),
        "allowed_updates": (
            allowed_updates if allowed_updates is not None else list(ALLOWED_UPDATES)
        ),
    }
    data = bot_post(
        "getUpdates",
        payload,
        token=token,
        http_post=http_post,
        timeout=float(timeout) + 10.0,
    )
    result = data.get("result")
    return result if isinstance(result, list) else []


def answer_callback_query(
    callback_query_id: str,
    *,
    token: str,
    http_post: HttpPost | None = None,
    text: str = "",
) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    bot_post("answerCallbackQuery", payload, token=token, http_post=http_post)


def edit_message_text(
    chat_id: str | int,
    message_id: int,
    text: str,
    *,
    token: str,
    http_post: HttpPost | None = None,
    reply_markup: dict | None = None,
) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    bot_post("editMessageText", payload, token=token, http_post=http_post)


def pager_markup(guest: list[dict]) -> dict:
    rows: list[list[dict]] = []
    row: list[dict] = []
    for i, item in enumerate(guest, 1):
        job_id = int(item["id"])
        row.append({"text": str(i), "callback_data": f"o:{job_id}"})
        if len(row) == 6:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


def action_markup(
    job_id: int,
    apply_url: str,
    *,
    status_buttons: bool = True,
    why_me: bool = True,
    mail: bool = True,
) -> dict:
    keyboard: list[list[dict]] = []
    url = (apply_url or "").strip()
    if url.lower().startswith("http://") or url.lower().startswith("https://"):
        keyboard.append([{"text": "Apply ↗", "url": url}])
    if status_buttons:
        keyboard.append(
            [
                {"text": "Save", "callback_data": f"s:{job_id}"},
                {"text": "Applied ✓", "callback_data": f"a:{job_id}"},
                {"text": "Hide ✗", "callback_data": f"h:{job_id}"},
            ]
        )
    extra: list[dict] = []
    if why_me:
        extra.append({"text": "Why me?", "callback_data": f"w:{job_id}"})
    if mail:
        extra.append({"text": "Mail", "callback_data": f"m:{job_id}"})
    if extra:
        keyboard.append(extra)
    return {"inline_keyboard": keyboard}


def send_blurb(
    text: str,
    *,
    token: str,
    chat_id: str,
    http_post: HttpPost | None = None,
) -> None:
    send_message(text, token=token, chat_id=chat_id, http_post=http_post)


def _gate_send(
    *, required: bool, http_post: HttpPost | None
) -> tuple[str, str] | None:
    token, chat_id = credentials()
    missing = not token or not chat_id
    if missing:
        if required and require_telegram():
            print(
                "TELEGRAM FAILED missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID",
                file=sys.stderr,
            )
            log.error("telegram skip missing credentials required")
            raise TelegramError("missing credentials")
        log.debug("telegram skip missing credentials")
        return None
    return token, chat_id


def deliver(
    text: str,
    *,
    required: bool = False,
    http_post: HttpPost | None = None,
) -> bool:
    """POST plain-text parts via Bot API. Returns False if a required send failed.

    Local missing token: skip send (caller already printed). Hosted cron sets
    JOBDIGEST_REQUIRE_TELEGRAM so a required send without creds fails loud.
    """
    if not (text or "").strip():
        log.debug("telegram skip empty")
        return True
    parts = split_message(text)
    try:
        creds = _gate_send(required=required, http_post=http_post)
    except TelegramError:
        return False
    if creds is None:
        return True
    token, chat_id = creds
    log.info("telegram send attempted parts=%s", len(parts))
    try:
        for part in parts:
            send_message(part, token=token, chat_id=chat_id, http_post=http_post)
    except TelegramError as exc:
        print(f"TELEGRAM FAILED {exc}", file=sys.stderr)
        log.error("telegram send fail")
        return False
    return True


def send_guest_pager(
    guest: list[dict],
    *,
    required: bool = False,
    http_post: HttpPost | None = None,
) -> bool:
    if not guest:
        return True
    try:
        creds = _gate_send(required=required, http_post=http_post)
    except TelegramError:
        return False
    if creds is None:
        return True
    token, chat_id = creds
    try:
        send_message(
            PAGER_TEXT,
            token=token,
            chat_id=chat_id,
            http_post=http_post,
            reply_markup=pager_markup(guest),
        )
    except TelegramError as exc:
        print(f"TELEGRAM FAILED {exc}", file=sys.stderr)
        log.error("telegram send fail")
        return False
    return True


def send_action_card(
    text: str,
    *,
    job_id: int,
    apply_url: str,
    required: bool = False,
    http_post: HttpPost | None = None,
    status_buttons: bool = True,
    why_me: bool = True,
    mail: bool = True,
) -> bool:
    if not (text or "").strip():
        return True
    try:
        creds = _gate_send(required=required, http_post=http_post)
    except TelegramError:
        return False
    if creds is None:
        return True
    token, chat_id = creds
    markup = action_markup(
        job_id, apply_url, status_buttons=status_buttons, why_me=why_me, mail=mail
    )
    reply = markup if markup.get("inline_keyboard") else None
    try:
        send_message(
            text.strip("\n"),
            token=token,
            chat_id=chat_id,
            http_post=http_post,
            reply_markup=reply,
        )
    except TelegramError as exc:
        print(f"TELEGRAM FAILED {exc}", file=sys.stderr)
        log.error("telegram send fail")
        return False
    return True
