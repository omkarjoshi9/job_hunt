from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

UA = "JobHunt/0.1"
HOST_SLEEP_S = 3.5
FETCH_HTTP = "http"
FETCH_DYNAMIC = "dynamic"
FETCH_STEALTH = "stealth"
ALLOWED_FETCH = frozenset({FETCH_HTTP, FETCH_DYNAMIC, FETCH_STEALTH})

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


def portal_fetch_mode(portal: dict) -> str:
    raw = str(portal.get("fetch") or FETCH_HTTP).strip().lower()
    return raw or FETCH_HTTP


def _enter(factory: Any) -> Any:
    session = factory
    if hasattr(factory, "__enter__"):
        session = factory.__enter__()
    _SESSION_CMS[id(session)] = factory
    return session


def open_session():
    try:
        from scrapling.fetchers import FetcherSession
    except Exception as exc:
        raise RuntimeError(
            "FetcherSession unavailable; install scrapling[fetchers] and playwright wheels"
        ) from exc
    factory = FetcherSession(
        impersonate="chrome",
        timeout=30,
        retries=3,
        stealthy_headers=False,
    )
    return _enter(factory)


def open_dynamic_session():
    try:
        from scrapling.fetchers import DynamicSession
    except Exception as exc:
        raise RuntimeError(
            "DynamicSession unavailable; install Chromium with python -m playwright install chromium"
        ) from exc
    factory = DynamicSession(
        headless=True,
        timeout=30000,
        google_search=False,
        extra_headers={"User-Agent": UA},
    )
    return _enter(factory)


def open_stealth_session():
    try:
        from scrapling.fetchers import StealthySession
    except Exception as exc:
        raise RuntimeError(
            "StealthySession unavailable; install Chromium with python -m playwright install chromium"
        ) from exc
    factory = StealthySession(
        headless=True,
        timeout=60000,
        google_search=False,
        extra_headers={"User-Agent": UA},
    )
    return _enter(factory)


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


@dataclass
class SessionBundle:
    http: Any = None
    dynamic: Any = None
    stealth: Any = None
    errors: dict[str, str] = field(default_factory=dict)

    def get(self, mode: str) -> Any:
        if mode == FETCH_HTTP:
            return self.http
        if mode == FETCH_DYNAMIC:
            return self.dynamic
        if mode == FETCH_STEALTH:
            return self.stealth
        return None


def open_bundle(modes: Iterable[str]) -> SessionBundle:
    bundle = SessionBundle()
    wanted = {str(mode) for mode in modes}
    if FETCH_HTTP in wanted:
        try:
            bundle.http = open_session()
        except Exception as exc:
            bundle.errors[FETCH_HTTP] = str(exc)
    if FETCH_DYNAMIC in wanted:
        try:
            bundle.dynamic = open_dynamic_session()
        except Exception as exc:
            bundle.errors[FETCH_DYNAMIC] = str(exc)
    if FETCH_STEALTH in wanted:
        try:
            bundle.stealth = open_stealth_session()
        except Exception as exc:
            bundle.errors[FETCH_STEALTH] = str(exc)
    return bundle


def close_bundle(bundle: SessionBundle | None) -> None:
    if bundle is None:
        return
    close_session(bundle.http)
    close_session(bundle.dynamic)
    close_session(bundle.stealth)


def _page_html(page: Any) -> str:
    html = (
        getattr(page, "html_content", None)
        or getattr(page, "html", None)
        or getattr(page, "body", None)
        or str(page)
    )
    if isinstance(html, bytes):
        return html.decode("utf-8", errors="replace")
    return str(html)


def get_html(
    session,
    url: str,
    *,
    sleep: Callable[[float], None],
    last_host: dict,
    fetch: str = FETCH_HTTP,
    solve_cloudflare: bool = False,
    extra_headers: dict | None = None,
) -> tuple[int, str, Any]:
    """Return (http_status, html_text, page). Rate-limit by hostname."""
    host = (urlsplit(url).hostname or "").lower()
    prev = last_host.get("host")
    if prev and prev == host:
        sleep(HOST_SLEEP_S)
    last_host["host"] = host
    headers = {"User-Agent": UA}
    if extra_headers:
        headers.update(extra_headers)
    mode = (fetch or FETCH_HTTP).strip().lower() or FETCH_HTTP
    if mode in {FETCH_DYNAMIC, FETCH_STEALTH}:
        kwargs: dict[str, Any] = {
            "extra_headers": headers,
            "google_search": False,
        }
        if mode == FETCH_STEALTH:
            kwargs["solve_cloudflare"] = bool(solve_cloudflare)
        page = session.fetch(url, **kwargs)
    else:
        page = session.get(
            url,
            headers=headers,
            stealthy_headers=False,
        )
    status = int(getattr(page, "status", 0) or 0)
    return status, _page_html(page), page
