from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

UA = "JobHunt/0.1"
HOST_SLEEP_S = 3.5

_CHALLENGE = (
    "just a moment",
    "attention required",
    "cf-challenge",
    "turnstile",
    "checking your browser",
)

_SESSION_CMS: dict[int, Any] = {}


def is_challenge(html: str, status: int) -> bool:
    if status in (403, 429, 503):
        return True
    blob = (html or "").lower()
    return any(s in blob for s in _CHALLENGE)


def open_session():
    try:
        from scrapling.fetchers import FetcherSession
    except Exception as exc:
        raise RuntimeError(
            "FetcherSession unavailable (scrapling fetchers import Playwright even for HTTP); "
            "keep HTTP-only pins scrapling+curl_cffi+browserforge; do not install Playwright"
        ) from exc
    factory = FetcherSession(
        impersonate="chrome",
        timeout=30,
        retries=3,
        stealthy_headers=False,
    )
    session = factory
    if hasattr(factory, "__enter__"):
        session = factory.__enter__()
    _SESSION_CMS[id(session)] = factory
    return session


def close_session(session: Any) -> None:
    if session is None:
        return
    factory = _SESSION_CMS.pop(id(session), None)
    if factory is not None and hasattr(factory, "__exit__"):
        factory.__exit__(None, None, None)
        return
    closer = getattr(session, "close", None)
    if callable(closer):
        closer()


def get_html(
    session,
    url: str,
    *,
    sleep: Callable[[float], None],
    last_host: dict,
) -> tuple[int, str, Any]:
    """Return (http_status, html_text, page). Rate-limit by hostname."""
    host = (urlsplit(url).hostname or "").lower()
    prev = last_host.get("host")
    if prev and prev == host:
        sleep(HOST_SLEEP_S)
    last_host["host"] = host
    page = session.get(
        url,
        headers={"User-Agent": UA},
        stealthy_headers=False,
    )
    status = int(getattr(page, "status", 0) or 0)
    html = (
        getattr(page, "html", None)
        or getattr(page, "body", None)
        or str(page)
    )
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    return status, str(html), page
