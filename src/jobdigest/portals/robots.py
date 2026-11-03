from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from protego import Protego

from jobdigest.portals.session import UA


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def fetch_robots(session, origin: str) -> str:
    """Return robots.txt text, or "" on any fetch error.

    NOTE: fail-open by design. An empty body is treated as "allow all" by
    `allowed`, so a network error or 5xx on robots.txt lets the portal be
    fetched. Failing closed would silently disable portals on transient errors.
    """
    url = origin.rstrip("/") + "/robots.txt"
    try:
        if session is not None and hasattr(session, "get"):
            page = session.get(
                url,
                headers={"User-Agent": UA},
                stealthy_headers=False,
            )
            body = getattr(page, "html", None) or getattr(page, "body", None) or ""
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="replace")
            return str(body)
        response = httpx.get(
            url, headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True
        )
        return response.text
    except Exception:
        return ""


def allowed(url: str, robots_txt: str, *, user_agent: str = UA) -> bool:
    if not (robots_txt or "").strip():
        return True
    rp = Protego.parse(robots_txt)
    return bool(rp.can_fetch(url, user_agent))


def check_url(
    url: str,
    *,
    cache: dict[str, str],
    session=None,
    allowed_fn: Callable[[str], bool] | None = None,
) -> bool:
    if allowed_fn is not None:
        return bool(allowed_fn(url))
    origin = origin_of(url)
    if origin not in cache:
        cache[origin] = fetch_robots(session, origin)
    return allowed(url, cache[origin])
