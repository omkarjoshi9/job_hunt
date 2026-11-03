from __future__ import annotations

import random
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

USER_AGENT = "JobHunt/0.1"
DEFAULT_TIMEOUT = 30.0
JITTER_MIN_S = 0.2
JITTER_MAX_S = 0.4
RETRY_AFTER_CAP_S = 30.0
DEFAULT_RETRY_AFTER_S = 2.0
MAX_TRANSIENT_RETRIES = 2
BACKOFF_BASE_S = 0.5


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
    )


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return DEFAULT_RETRY_AFTER_S
    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return DEFAULT_RETRY_AFTER_S
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, min(seconds, RETRY_AFTER_CAP_S))


def request_get(
    client: httpx.Client,
    url: str,
    *,
    sleep: Callable[[float], None],
) -> httpx.Response:
    """GET with polite jitter, one 429 retry, and up to 2 retries on 5xx/timeouts."""
    sleep(random.uniform(JITTER_MIN_S, JITTER_MAX_S))
    rate_limited = False
    transient = 0
    while True:
        try:
            response = client.get(url)
        except httpx.TimeoutException:
            if transient >= MAX_TRANSIENT_RETRIES:
                raise
            transient += 1
            sleep(BACKOFF_BASE_S * 2 ** (transient - 1))
            continue
        if response.status_code == 429 and not rate_limited:
            rate_limited = True
            sleep(_retry_after_seconds(response))
            continue
        if response.status_code >= 500 and transient < MAX_TRANSIENT_RETRIES:
            transient += 1
            sleep(BACKOFF_BASE_S * 2 ** (transient - 1))
            continue
        return response


def looks_like_html(response: httpx.Response) -> bool:
    ctype = (response.headers.get("content-type") or "").lower()
    if "text/html" in ctype:
        return True
    start = response.text.lstrip()[:64].lower()
    return start.startswith("<!doctype") or start.startswith("<html")


def parse_json_or_none(response: httpx.Response) -> Any | None:
    if looks_like_html(response):
        return None
    try:
        return response.json()
    except ValueError:
        return None
