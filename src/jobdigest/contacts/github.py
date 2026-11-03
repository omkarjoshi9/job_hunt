from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from jobdigest.ats.http_client import USER_AGENT
from jobdigest.contacts.types import DiscoveredContact
from jobdigest.normalize import collapse_ws

log = logging.getLogger("jobdigest.contacts.github")

_BIO_RE = re.compile(r"recruit|talent|campus|university|hiring|people|\bhr\b", re.I)
_MEMBER_CAP = 20


def github_token() -> str:
    return (os.environ.get("JOBDIGEST_GITHUB_TOKEN") or "").strip()


def github_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    auth = (token if token is not None else github_token()).strip()
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    return headers


def _guess_role(bio: str) -> str:
    text = (bio or "").lower()
    if re.search(r"recruit|talent|\bhr\b|people", text):
        return "Recruiter"
    if re.search(r"campus|university|hiring", text):
        return "University Recruiter"
    return "Recruiter"


def _fetch_members(
    slug: str, client: httpx.Client, headers: dict[str, str]
) -> list[Any]:
    try:
        resp = client.get(
            f"https://api.github.com/orgs/{slug}/members?per_page=30",
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            log.info("github members status=%s org=%s", resp.status_code, slug)
            return []
        members = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("github members fetch failed org=%s: %s", slug, exc)
        return []
    return members if isinstance(members, list) else []


def _fetch_user(
    login: str, client: httpx.Client, headers: dict[str, str]
) -> dict[str, Any] | None:
    try:
        uresp = client.get(
            f"https://api.github.com/users/{login}",
            headers=headers,
            timeout=5,
        )
        if uresp.status_code != 200:
            return None
        data = uresp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("github user fetch failed login=%s: %s", login, exc)
        return None
    return data if isinstance(data, dict) else None


def _contact_from_user(data: dict[str, Any]) -> DiscoveredContact | None:
    bio = str(data.get("bio") or "")
    if not _BIO_RE.search(bio):
        return None
    name = collapse_ws(data.get("name") or "")
    if not name:
        return None
    email = collapse_ws(data.get("email") or "").lower() or None
    if email and "noreply" in email:
        email = None
    return DiscoveredContact(
        name=name,
        source="github",
        role=_guess_role(bio),
        email=email,
        confidence=70.0,
    )


def github_org_recruiters(
    org_slug: str,
    client: httpx.Client,
    *,
    token: str | None = None,
) -> list[DiscoveredContact]:
    slug = (org_slug or "").strip()
    if not slug:
        return []
    headers = github_headers(token)
    out: list[DiscoveredContact] = []
    seen: set[str] = set()
    for member in _fetch_members(slug, client, headers)[:_MEMBER_CAP]:
        login = member.get("login") if isinstance(member, dict) else None
        if not login:
            continue
        data = _fetch_user(login, client, headers)
        contact = _contact_from_user(data) if data is not None else None
        if contact is None:
            continue
        key = contact.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(contact)
    return out
