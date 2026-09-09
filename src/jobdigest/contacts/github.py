from __future__ import annotations

import os
import re

import httpx

from jobdigest.ats.http_client import USER_AGENT
from jobdigest.contacts.types import DiscoveredContact
from jobdigest.normalize import collapse_ws

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
    try:
        resp = client.get(
            f"https://api.github.com/orgs/{slug}/members?per_page=30",
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        members = resp.json()
        if not isinstance(members, list):
            return []
    except Exception:
        return []

    out: list[DiscoveredContact] = []
    seen: set[str] = set()
    for member in members[:_MEMBER_CAP]:
        login = (member or {}).get("login") if isinstance(member, dict) else None
        if not login:
            continue
        try:
            uresp = client.get(
                f"https://api.github.com/users/{login}",
                headers=headers,
                timeout=5,
            )
            if uresp.status_code != 200:
                continue
            data = uresp.json()
        except Exception:
            continue
        bio = str(data.get("bio") or "")
        if not _BIO_RE.search(bio):
            continue
        name = collapse_ws(data.get("name") or "")
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        email = collapse_ws(data.get("email") or "").lower() or None
        if email and "noreply" in email:
            email = None
        out.append(
            DiscoveredContact(
                name=name,
                source="github",
                role=_guess_role(bio),
                email=email,
                confidence=70.0,
            )
        )
    return out
