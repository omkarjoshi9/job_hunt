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
    SessionBundle,
    close_bundle,
    close_session,
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
    name = str(portal.get("name") or "")
    url = str(portal.get("url") or "")
    parser_name = str(portal.get("parser") or "")
    parser = PARSERS.get(parser_name)
    if parser is None:
        return _error(
            name=name,
            url=url,
            error_class="parse",
            detail=f"unknown parser {parser_name!r}",
        )

    fetch = portal_fetch_mode(portal)
    if fetch not in ALLOWED_FETCH:
        return _error(
            name=name,
            url=url,
            error_class="parse",
            detail=f"unknown fetch {fetch!r}",
        )
    solve_cf = bool(portal.get("solve_cloudflare"))

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
        if get_html_fn is not None:
            fetched = get_html_fn(
                session,
                url,
                sleep=sleep,
                last_host=last_host,
                fetch=fetch,
                solve_cloudflare=solve_cf,
            )
        else:
            active = session
            if active is None and sessions is not None:
                if fetch in sessions.errors:
                    return _error(
                        name=name,
                        url=url,
                        error_class="http",
                        detail=sessions.errors[fetch],
                    )
                active = sessions.get(fetch)
            if active is None:
                if fetch != FETCH_HTTP:
                    return _error(
                        name=name,
                        url=url,
                        error_class="http",
                        detail=f"no session for fetch {fetch!r}",
                    )
                active = open_session()
            fetched = get_html(
                active,
                url,
                sleep=sleep,
                last_host=last_host,
                fetch=fetch,
                solve_cloudflare=solve_cf,
            )
        if len(fetched) == 2:
            status, html = fetched
            page: Any = None
        else:
            status, html, page = fetched[0], fetched[1], fetched[2]
    except TypeError:
        if get_html_fn is not None:
            fetched = get_html_fn(session, url, sleep=sleep, last_host=last_host)
            if len(fetched) == 2:
                status, html = fetched
                page = None
            else:
                status, html, page = fetched[0], fetched[1], fetched[2]
        else:
            raise
    except Exception as exc:
        klass = "timeout" if "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower() else "http"
        return _error(name=name, url=url, error_class=klass, detail=str(exc))

    status_i = int(status or 0)
    html_s = str(html or "")
    if is_challenge(html_s, status_i):
        return _error(
            name=name,
            url=url,
            error_class="cloudflare",
            detail="challenge page",
            http_status=status_i,
        )
    if status_i not in (0, 200):
        return _error(
            name=name,
            url=url,
            error_class="http",
            detail=f"http {status_i}",
            http_status=status_i,
        )

    target = page if page is not None and hasattr(page, "css") else html_s
    try:
        jobs = parser(target, source=name, base_url=url)
    except Exception as exc:
        return _error(
            name=name,
            url=url,
            error_class="parse",
            detail=str(exc),
            http_status=status_i,
        )
    if not jobs:
        return _error(
            name=name,
            url=url,
            error_class="parse",
            detail="0 cards",
            http_status=status_i or 200,
        )
    return PortalResult(status="ok", jobs=jobs, http_status=status_i or 200, name=name, url=url)


__all__ = [
    "ALLOWED_FETCH",
    "FETCH_DYNAMIC",
    "FETCH_HTTP",
    "FETCH_STEALTH",
    "PARSERS",
    "PortalResult",
    "SessionBundle",
    "close_bundle",
    "close_session",
    "is_challenge",
    "jobs_from_cards",
    "open_bundle",
    "open_session",
    "portal_fetch_mode",
    "run_one",
]
