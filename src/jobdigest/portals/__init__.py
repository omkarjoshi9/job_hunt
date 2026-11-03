from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jobdigest.ats.base import NormalizedJob
from jobdigest.portals import foss_united, hasjob, null_community
from jobdigest.portals.generic import jobs_from_cards, parse as parse_generic
from jobdigest.portals.robots import check_url
from jobdigest.portals.session import (
    ALLOWED_FETCH,
    FETCH_DYNAMIC,
    FETCH_HTTP,
    FETCH_STEALTH,
    OpenedSession,
    SessionBundle,
    close_bundle,
    get_html,
    is_challenge,
    open_bundle,
    open_session,
    portal_fetch_mode,
)

PARSERS: dict[str, Callable[..., list[NormalizedJob]]] = {
    "foss_united": foss_united.parse,
    "null_community": null_community.parse,
    "hasjob": hasjob.parse,
    "generic_cards": parse_generic,
}


@dataclass
class PortalResult:
    status: str
    jobs: list[NormalizedJob] = field(default_factory=list)
    error_class: str | None = None
    http_status: int | None = None
    detail: str | None = None
    name: str = ""
    url: str = ""


def _error(
    *,
    name: str,
    url: str,
    error_class: str,
    detail: str,
    http_status: int | None = None,
) -> PortalResult:
    return PortalResult(
        status="error",
        error_class=error_class,
        http_status=http_status,
        detail=detail,
        name=name,
        url=url,
    )


class _Failure(Exception):
    """Internal: carries a PortalResult error out of a run_one stage."""

    def __init__(self, result: PortalResult) -> None:
        super().__init__(result.detail)
        self.result = result


def _unpack_fetched(fetched: tuple) -> tuple[int, str, Any]:
    """Accept (status, html) or (status, html, page) from get_html or an injected hook."""
    if len(fetched) == 2:
        return fetched[0], fetched[1], None
    return fetched[0], fetched[1], fetched[2]


def _resolve_session(
    *, name: str, url: str, fetch: str, session, sessions: SessionBundle | None
) -> tuple[Any, OpenedSession | None]:
    """Pick the session for `fetch`. Second item is a session we opened and must close."""
    if session is not None:
        return session, None
    if sessions is not None:
        if fetch in sessions.errors:
            raise _Failure(
                _error(name=name, url=url, error_class="http", detail=sessions.errors[fetch])
            )
        active = sessions.get(fetch)
        if active is not None:
            return active, None
    if fetch != FETCH_HTTP:
        raise _Failure(
            _error(
                name=name,
                url=url,
                error_class="http",
                detail=f"no session for fetch {fetch!r}",
            )
        )
    opened = open_session()
    return opened.session, opened


def _fetch_page(
    *,
    name: str,
    url: str,
    fetch: str,
    solve_cf: bool,
    extra_headers: dict | None,
    session,
    sessions: SessionBundle | None,
    sleep: Callable[[float], None],
    last_host: dict,
    get_html_fn: Callable[..., tuple] | None,
) -> tuple[int, str, Any]:
    """Fetch the page. Any failure becomes a _Failure with class http/timeout."""
    opened: OpenedSession | None = None
    try:
        fetcher = get_html_fn
        active = session
        if fetcher is None:
            active, opened = _resolve_session(
                name=name, url=url, fetch=fetch, session=session, sessions=sessions
            )
            fetcher = get_html
        fetched = fetcher(
            active,
            url,
            sleep=sleep,
            last_host=last_host,
            fetch=fetch,
            solve_cloudflare=solve_cf,
            extra_headers=extra_headers,
        )
        return _unpack_fetched(fetched)
    except _Failure:
        raise
    except Exception as exc:
        klass = (
            "timeout"
            if "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower()
            else "http"
        )
        raise _Failure(_error(name=name, url=url, error_class=klass, detail=str(exc))) from exc
    finally:
        if opened is not None:
            opened.close()


def _check_response(*, name: str, url: str, status: int, html: str) -> None:
    """Raise _Failure for a challenge page or a non-200 status."""
    if is_challenge(html, status):
        raise _Failure(
            _error(
                name=name,
                url=url,
                error_class="cloudflare",
                detail="challenge page",
                http_status=status,
            )
        )
    if status not in (0, 200):
        raise _Failure(
            _error(
                name=name,
                url=url,
                error_class="http",
                detail=f"http {status}",
                http_status=status,
            )
        )


def _parse_jobs(
    parser: Callable[..., list[NormalizedJob]],
    *,
    name: str,
    url: str,
    status: int,
    html: str,
    page: Any,
) -> list[NormalizedJob]:
    target = page if page is not None and hasattr(page, "css") else html
    try:
        jobs = parser(target, source=name, base_url=url)
    except Exception as exc:
        raise _Failure(
            _error(name=name, url=url, error_class="parse", detail=str(exc), http_status=status)
        ) from exc
    if not jobs:
        raise _Failure(
            _error(
                name=name,
                url=url,
                error_class="parse",
                detail="0 cards",
                http_status=status or 200,
            )
        )
    return jobs


def run_one(
    portal: dict,
    *,
    session=None,
    sessions: SessionBundle | None = None,
    sleep: Callable[[float], None] | None = None,
    last_host: dict | None = None,
    robots_cache: dict[str, str] | None = None,
    allowed_fn: Callable[[str], bool] | None = None,
    get_html_fn: Callable[..., tuple] | None = None,
) -> PortalResult:
    """Fetch and parse one portal.

    `get_html_fn`, when given, replaces session.get_html and is called as
    `get_html_fn(session, url, sleep=, last_host=, fetch=, solve_cloudflare=,
    extra_headers=)`, returning (status, html) or (status, html, page).
    """
    name = str(portal.get("name") or "")
    url = str(portal.get("url") or "")
    parser_name = str(portal.get("parser") or "")
    parser = PARSERS.get(parser_name)
    if parser is None:
        return _error(
            name=name, url=url, error_class="parse", detail=f"unknown parser {parser_name!r}"
        )

    fetch = portal_fetch_mode(portal)
    if fetch not in ALLOWED_FETCH:
        return _error(name=name, url=url, error_class="parse", detail=f"unknown fetch {fetch!r}")

    if sleep is None:
        import time

        sleep = time.sleep
    if last_host is None:
        last_host = {}
    if robots_cache is None:
        robots_cache = {}

    if not check_url(url, cache=robots_cache, session=session, allowed_fn=allowed_fn):
        return _error(name=name, url=url, error_class="robots", detail="disallowed by robots.txt")

    try:
        status, html, page = _fetch_page(
            name=name,
            url=url,
            fetch=fetch,
            solve_cf=bool(portal.get("solve_cloudflare")),
            extra_headers={"Accept": "application/json"} if parser_name == "hasjob" else None,
            session=session,
            sessions=sessions,
            sleep=sleep,
            last_host=last_host,
            get_html_fn=get_html_fn,
        )
        status_i = int(status or 0)
        html_s = str(html or "")
        _check_response(name=name, url=url, status=status_i, html=html_s)
        jobs = _parse_jobs(parser, name=name, url=url, status=status_i, html=html_s, page=page)
    except _Failure as failure:
        return failure.result
    return PortalResult(status="ok", jobs=jobs, http_status=status_i or 200, name=name, url=url)


__all__ = [
    "ALLOWED_FETCH",
    "FETCH_DYNAMIC",
    "FETCH_HTTP",
    "FETCH_STEALTH",
    "PARSERS",
    "PortalResult",
    "OpenedSession",
    "SessionBundle",
    "close_bundle",
    "is_challenge",
    "jobs_from_cards",
    "open_bundle",
    "open_session",
    "portal_fetch_mode",
    "run_one",
]
