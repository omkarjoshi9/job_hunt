from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

import httpx

from jobdigest.contacts.github import github_headers

log = logging.getLogger("jobdigest.contacts.pattern")

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
    org_slug = host.split(".")[0]
    found_emails: list[str] = []
    headers = github_headers(token)
    try:
        search_url = (
            "https://api.github.com/search/commits"
            f"?q={quote_plus(host)}+committer-email:{host}&per_page=10"
        )
        resp = client.get(search_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            for item in items:
                commit = item.get("commit", {})
                for role in ("author", "committer"):
                    email = commit.get(role, {}).get("email", "") or ""
                    if f"@{host}" in email.lower() and "noreply" not in email.lower():
                        found_emails.append(email)
    except Exception as exc:
        log.debug("github commit search failed: %s", exc)

    if not found_emails:
        try:
            members_url = f"https://api.github.com/orgs/{org_slug}/members?per_page=10"
            resp = client.get(members_url, headers=headers, timeout=8)
            if resp.status_code == 200:
                members = resp.json()
                for member in members[:5]:
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
                            found_emails.append(str(email))
        except Exception as exc:
            log.debug("github org member emails failed: %s", exc)

    votes: dict[str, int] = {}
    for email in found_emails:
        inferred = _infer_pattern_from_email(email, host)
        if inferred:
            votes[inferred] = votes.get(inferred, 0) + 1
    if votes:
        return max(votes, key=lambda key: votes[key])
    return fallback
