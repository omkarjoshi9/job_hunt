from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

import httpx

USER_AGENT = "JobHunt/0.1"
DEFAULT_TIMEOUT = 30.0
JITTER_MIN_S = 0.2
JITTER_MAX_S = 0.4
RETRY_AFTER_CAP_S = 30.0


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=False,
    )


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return 2.0
    try:
        return min(float(raw), RETRY_AFTER_CAP_S)
    except ValueError:
        return 2.0


def request_get(
    client: httpx.Client,
    url: str,
    *,
    sleep: Callable[[float], None],
    jitter: bool = True,
) -> httpx.Response:
    if jitter:
        sleep(random.uniform(JITTER_MIN_S, JITTER_MAX_S))
    response = client.get(url)
    if response.status_code == 429:
        sleep(_retry_after_seconds(response))
        response = client.get(url)
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
