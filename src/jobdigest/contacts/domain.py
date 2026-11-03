from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

import httpx

from jobdigest.ats.http_client import USER_AGENT

log = logging.getLogger("jobdigest.contacts.domain")

_CLEARBIT = "https://autocomplete.clearbit.com/v1/companies/suggest?query="
_SUFFIXES = ("com", "io", "co.in", "in")


def _slug(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (company or "").lower())


def _clearbit_domain(name: str, client: httpx.Client) -> str | None:
    try:
        resp = client.get(
            _CLEARBIT + quote_plus(name),
            headers={"User-Agent": USER_AGENT},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        suggestions = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("clearbit lookup failed company=%r: %s", name, exc)
        return None
    if not isinstance(suggestions, list) or not suggestions:
        return None
    first = suggestions[0]
    domain = first.get("domain") if isinstance(first, dict) else None
    if not domain:
        return None
    return str(domain).strip().lower() or None


def _final_host_matches(resp: httpx.Response, slug: str) -> bool:
    """Reject parked pages / redirects to an unrelated host."""
    host = _slug(resp.url.host or "")
    return bool(host) and slug in host


def _probe_domain(slug: str, client: httpx.Client) -> str | None:
    for suffix in _SUFFIXES:
        candidate = f"{slug}.{suffix}"
        try:
            resp = client.get(
                f"https://{candidate}",
                headers={"User-Agent": USER_AGENT},
                timeout=5,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            log.debug("domain probe %s failed: %s", candidate, exc)
            continue
        if resp.status_code >= 400:
            continue
        if not _final_host_matches(resp, slug):
            log.info("domain probe %s redirected to %s; rejected", candidate, resp.url.host)
            continue
        return candidate
    return None


def domain_inference(company: str, client: httpx.Client) -> str | None:
    name = (company or "").strip()
    if not name:
        return None
    domain = _clearbit_domain(name, client)
    if domain:
        return domain
    slug = _slug(name)
    if not slug:
        return None
    return _probe_domain(slug, client)
