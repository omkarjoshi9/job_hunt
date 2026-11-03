from __future__ import annotations

import logging
from typing import Any

import httpx

from jobdigest.ats.http_client import make_client
from jobdigest.contacts.domain import domain_inference
from jobdigest.contacts.github import github_org_recruiters, github_token
from jobdigest.contacts.jd import parse_jd_names
from jobdigest.contacts.pattern import email_pattern_detection
from jobdigest.contacts.rank import merge_contacts
from jobdigest.contacts.types import DiscoveredContact
from jobdigest.normalize import collapse_ws

log = logging.getLogger("jobdigest.contacts.pipeline")


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def run_pipeline(
    *,
    company: str,
    description_text: str | None = None,
    client: httpx.Client | None = None,
    github_token_value: str | None = None,
) -> list[DiscoveredContact]:
    own = client is None
    http = client or make_client()
    token = (
        github_token_value
        if github_token_value is not None
        else github_token()
    )
    try:
        jd = parse_jd_names(description_text)
        domain = domain_inference(company, http)
        pattern = None
        if domain:
            try:
                pattern = email_pattern_detection(domain, http, token=token)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("pattern detection failed domain=%s: %s", domain, exc)
                pattern = f"{{first}}.{{last}}@{domain}"
        github: list[DiscoveredContact] = []
        if token and domain:
            org_slug = domain.split(".")[0]
            try:
                github = github_org_recruiters(org_slug, http, token=token)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("github org recruiters failed org=%s: %s", org_slug, exc)
        elif not token:
            log.info("contacts github skip missing JOBDIGEST_GITHUB_TOKEN")
        return merge_contacts(
            jd=jd,
            github=github,
            company=company,
            domain=domain,
            pattern=pattern,
        )
    except Exception:  # noqa: BLE001 - pipeline contract: never raise; logged with traceback
        log.exception("contacts pipeline failed company=%r", company)
        return []
    finally:
        if own:
            http.close()


def enrich_job(
    job_row: Any,
    *,
    client: httpx.Client | None = None,
    github_token_value: str | None = None,
) -> list[DiscoveredContact]:
    company = collapse_ws(_row_get(job_row, "company", "") or "")
    description = _row_get(job_row, "description_text", "") or ""
    return run_pipeline(
        company=company,
        description_text=str(description),
        client=client,
        github_token_value=github_token_value,
    )
