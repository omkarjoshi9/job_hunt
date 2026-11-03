from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any

from jobdigest.ats.base import NormalizedJob, decode_html_content, parse_posted_at

SITES = frozenset(
    {
        "linkedin",
        "indeed",
        "zip_recruiter",
        "glassdoor",
        "google",
        "bayt",
        "naukri",
        "bdjobs",
    }
)

ScrapeFn = Callable[..., Any]


def _http_url(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("http://") or text.lower().startswith("https://"):
        return text
    return ""


def _first_job_type(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.split(",")[0].strip() or None


def _workplace_hint(record: dict) -> str | None:
    wfh = str(record.get("work_from_home_type") or "").strip()
    if wfh:
        return wfh
    remote = record.get("is_remote")
    if remote is True or str(remote).strip().lower() in {"true", "1"}:
        return "remote"
    return None


def _posted_at(value: Any) -> str | None:
    """Normalize date/datetime/pandas Timestamp/str/NaT/None to an ISO-Z string."""
    if value is None:
        return None
    try:
        if value != value:  # NaN / NaT
            return None
    except (TypeError, ValueError):
        pass
    to_py = getattr(value, "to_pydatetime", None)
    if callable(to_py):
        try:
            value = to_py()
        except (ValueError, TypeError):
            return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return parse_posted_at(value.astimezone(timezone.utc).isoformat())
    if isinstance(value, date):
        return parse_posted_at(datetime(value.year, value.month, value.day).isoformat())
    return parse_posted_at(value)


def row_to_normalized(record: dict, *, source: str) -> NormalizedJob | None:
    title = str(record.get("title") or "").strip()
    if not title:
        return None
    job_url = _http_url(record.get("job_url"))
    direct = _http_url(record.get("job_url_direct"))
    apply_url = direct or job_url
    hosted_url = job_url or apply_url
    ext = str(record.get("id") or "").strip()
    if not ext and hosted_url:
        ext = sha256(hosted_url.encode("utf-8")).hexdigest()[:16]
    if not apply_url and not hosted_url and not ext:
        return None
    desc = record.get("description")
    desc_text = decode_html_content(str(desc)) if desc else ""
    return NormalizedJob(
        title=title,
        company=str(record.get("company") or "").strip(),
        location_raw=str(record.get("location") or "").strip(),
        apply_url=apply_url,
        hosted_url=hosted_url,
        employment_hint=_first_job_type(record.get("job_type")),
        workplace_hint=_workplace_hint(record),
        description_text=desc_text,
        source=source,
        ats="marketplace",
        external_id=ext,
        raw_json=json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str),
        posted_at=_posted_at(record.get("date_posted")),
    )


def _scrape_kwargs(row: dict, *, proxies: list[str] | None) -> dict[str, Any]:
    site = str(row.get("site") or "").strip().lower()
    fetch_desc = row.get("linkedin_fetch_description")
    if fetch_desc is None and site == "naukri":
        fetch_desc = True
    kwargs: dict[str, Any] = {
        "site_name": [site],
        "search_term": row.get("search_term") or None,
        "location": row.get("location") or None,
        "results_wanted": int(row.get("results_wanted") or 30),
        "verbose": 0,
        "linkedin_fetch_description": bool(fetch_desc),
    }
    google = row.get("google_search_term")
    if google:
        kwargs["google_search_term"] = str(google)
    country = row.get("country_indeed")
    if country:
        kwargs["country_indeed"] = str(country)
    hours_old = row.get("hours_old")
    if hours_old not in (None, ""):
        kwargs["hours_old"] = int(hours_old)
    job_type = row.get("job_type")
    if job_type:
        kwargs["job_type"] = str(job_type)
    if "is_remote" in row and row.get("is_remote") is not None:
        kwargs["is_remote"] = bool(row.get("is_remote"))
    if proxies:
        kwargs["proxies"] = proxies
    return kwargs


def _records_from_scrape(payload: Any) -> list[dict]:
    if payload is None:
        return []
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        empty = getattr(payload, "empty", False)
        if empty:
            return []
        rows = to_dict("records")
        return [row for row in rows if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def scrape_row(
    row: dict,
    *,
    proxies: list[str] | None = None,
    scrape_fn: ScrapeFn | None = None,
) -> list[NormalizedJob]:
    if scrape_fn is None:
        from jobspy import scrape_jobs as scrape_fn
    source = str(row.get("name") or "").strip()
    payload = scrape_fn(**_scrape_kwargs(row, proxies=proxies))
    jobs: list[NormalizedJob] = []
    for record in _records_from_scrape(payload):
        job = row_to_normalized(record, source=source)
        if job is not None:
            jobs.append(job)
    return jobs
