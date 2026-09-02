from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

import httpx

from jobdigest.logutil import get_logger

TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
SPLIT_LIMIT = 3800

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


def require_telegram() -> bool:
    raw = (os.environ.get("JOBDIGEST_REQUIRE_TELEGRAM") or "").strip().lower()
    return raw in {"1", "true", "yes"}


def _httpx_post(url: str, json: dict | None = None, timeout: float = 30.0) -> httpx.Response:
    with httpx.Client(timeout=timeout) as client:
        return client.post(url, json=json, timeout=timeout)


def send_message(
    text: str,
    *,
    token: str,
    chat_id: str,
    http_post: HttpPost | None = None,
    timeout: float = 30.0,
) -> None:
    url = TELEGRAM_SEND_URL.format(token=token)
    payload = {"chat_id": chat_id, "text": text}
    post = http_post if http_post is not None else _httpx_post
    try:
        resp = post(url, json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        log.error("telegram send fail transport")
        raise TelegramError(str(exc)) from exc
    except Exception as exc:
        log.error("telegram send fail")
        raise TelegramError(str(exc)) from exc
    status = int(getattr(resp, "status_code", 0) or 0)
    if status != 200:
        body = str(getattr(resp, "text", "") or "")[:500]
        log.error("telegram send fail http_status=%s", status)
        raise TelegramError(f"HTTP {status}: {body}")
    log.info("telegram send ok http_status=%s", status)


def deliver(
    text: str,
    *,
    required: bool = False,
    http_post: HttpPost | None = None,
) -> bool:
    """POST plain-text parts via Bot API. Returns False if a required send failed.

    Local missing token: skip send (caller already printed). Hosted cron sets
    JOBDIGEST_REQUIRE_TELEGRAM so a required send without creds fails loud.
    Default pytest never performs a live POST unless http_post is injected.
    """
    if not (text or "").strip():
        log.debug("telegram skip empty")
        return True
    parts = split_message(text)
    token, chat_id = credentials()
    missing = not token or not chat_id
    if missing:
        if required and require_telegram():
            print(
                "TELEGRAM FAILED missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID",
                file=sys.stderr,
            )
            log.error("telegram skip missing credentials required")
            return False
        log.debug("telegram skip missing credentials")
        return True
    if http_post is None and os.environ.get("PYTEST_CURRENT_TEST"):
        log.debug("telegram skip pytest")
        return True
    log.info("telegram send attempted parts=%s", len(parts))
    try:
        for part in parts:
            send_message(part, token=token, chat_id=chat_id, http_post=http_post)
    except TelegramError as exc:
        print(f"TELEGRAM FAILED {exc}", file=sys.stderr)
        log.error("telegram send fail")
        return False
    return True
