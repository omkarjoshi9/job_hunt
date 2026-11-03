from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

import httpx

from jobdigest.contacts.github import github_headers

log = logging.getLogger("jobdigest.contacts.pattern")

MIN_PATTERN_VOTES = 2
_EXPECTED_ERRORS = (httpx.HTTPError, ValueError, AttributeError, TypeError)

_PATTERN_REGEXES = [
    (re.compile(r"^([a-z]+)\.([a-z]+)@"), "{first}.{last}@{domain}"),
    (re.compile(r"^([a-z])([a-z]{3,})@"), "{fi}{last}@{domain}"),
    (re.compile(r"^([a-z]{3,})([a-z])@"), "{first}{li}@{domain}"),
    (re.compile(r"^([a-z]{3,})@"), "{first}@{domain}"),
]


def _infer_pattern_from_email(email: str, domain: str) -> str | None:
    local = email.split("@")[0].lower()
    for regex, template in _PATTERN_REGEXES:
        if regex.match(local + "@"):
            return template.replace("{domain}", domain)
    return None


def choose_pattern(
    emails: list[str], host: str, *, min_votes: int = MIN_PATTERN_VOTES
) -> str | None:
    """Most common inferred pattern, only if at least ``min_votes`` emails agree."""
    votes: dict[str, int] = {}
    for email in emails:
        inferred = _infer_pattern_from_email(email, host)
        if inferred:
            votes[inferred] = votes.get(inferred, 0) + 1
    if not votes:
        return None
    best = max(votes, key=lambda key: votes[key])
    return best if votes[best] >= min_votes else None


def _commit_emails(host: str, client: httpx.Client, headers: dict[str, str]) -> list[str]:
    found: list[str] = []
    search_url = (
        "https://api.github.com/search/commits"
        f"?q={quote_plus(host)}+committer-email:{host}&per_page=10"
    )
    try:
        resp = client.get(search_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return found
        for item in resp.json().get("items", []):
            commit = item.get("commit", {})
            for role in ("author", "committer"):
                email = commit.get(role, {}).get("email", "") or ""
                if f"@{host}" in email.lower() and "noreply" not in email.lower():
                    found.append(email)
    except _EXPECTED_ERRORS as exc:
        log.warning("github commit search failed host=%s: %s", host, exc)
    return found


def _member_emails(
    host: str, org_slug: str, client: httpx.Client, headers: dict[str, str]
) -> list[str]:
    found: list[str] = []
    try:
        resp = client.get(
            f"https://api.github.com/orgs/{org_slug}/members?per_page=10",
            headers=headers,
            timeout=8,
        )
        if resp.status_code != 200:
            return found
        for member in resp.json()[:5]:
            login = member.get("login")
            if not login:
                continue
            uresp = client.get(
                f"https://api.github.com/users/{login}",
                headers=headers,
                timeout=5,
            )
            if uresp.status_code == 200:
                email = uresp.json().get("email") or ""
                if f"@{host}" in str(email).lower():
                    found.append(str(email))
    except _EXPECTED_ERRORS as exc:
        log.warning("github org member emails failed org=%s: %s", org_slug, exc)
    return found


def email_pattern_detection(
    domain: str,
    client: httpx.Client,
    *,
    token: str | None = None,
) -> str:
    host = (domain or "").strip().lower()
    fallback = f"{{first}}.{{last}}@{host}" if host else "{first}.{last}@{domain}"
    if not host:
        return fallback
    headers = github_headers(token)
    emails = _commit_emails(host, client, headers)
    if not emails:
        emails = _member_emails(host, host.split(".")[0], client, headers)
    return choose_pattern(emails, host) or fallback
