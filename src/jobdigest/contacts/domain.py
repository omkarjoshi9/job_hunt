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


def domain_inference(company: str, client: httpx.Client) -> str | None:
    name = (company or "").strip()
    if not name:
        return None
    try:
        resp = client.get(
            _CLEARBIT + quote_plus(name),
            headers={"User-Agent": USER_AGENT},
            timeout=8,
        )
        if resp.status_code == 200:
            suggestions = resp.json()
            if isinstance(suggestions, list) and suggestions:
                domain = suggestions[0].get("domain") if isinstance(suggestions[0], dict) else None
                if domain:
                    return str(domain).strip().lower() or None
    except Exception as exc:
        log.debug("clearbit lookup failed: %s", exc)

    slug = _slug(name)
    if not slug:
        return None
    for suffix in _SUFFIXES:
        candidate = f"{slug}.{suffix}"
        try:
            resp = client.get(
                f"https://{candidate}",
                headers={"User-Agent": USER_AGENT},
                timeout=5,
                follow_redirects=True,
            )
            if resp.status_code < 400:
                return candidate
        except Exception:
            continue
    return None
