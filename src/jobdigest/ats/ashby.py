from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from jobdigest.ats.base import (
    CompanyConfig,
    FetchResult,
    FetchStatus,
    NormalizedJob,
    classify_http_status,
    decode_html_content,
    empty_or_ok,
    join_locations,
)
from jobdigest.ats.http_client import parse_json_or_none, request_get


def _location_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        loc = value.get("location") or value.get("name")
        return loc if isinstance(loc, str) else None
    return None


class AshbyAdapter:
    ats = "ashby"

    def __init__(self, client: httpx.Client, *, sleep: Callable[[float], None]) -> None:
        self._client = client
        self._sleep = sleep

    def fetch(self, company: CompanyConfig) -> FetchResult:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{company.token}"
        try:
            response = request_get(self._client, url, sleep=self._sleep)
        except httpx.HTTPError as exc:
            return FetchResult(FetchStatus.ERROR, None, [], error=str(exc))
        terminal = classify_http_status(response.status_code)
        if terminal is not None:
            return FetchResult(terminal, response.status_code, [], error=None)
        payload = parse_json_or_none(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return FetchResult(FetchStatus.ERROR, response.status_code, [], error="invalid ashby json")
        jobs = [
            self._map(company, item)
            for item in payload["jobs"]
            if isinstance(item, dict) and item.get("isListed") is not False
        ]
        return FetchResult(empty_or_ok(len(jobs)), response.status_code, jobs)

    def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob:
        loc = _location_text(item.get("location"))
        secondary = item.get("secondaryLocations") if isinstance(item.get("secondaryLocations"), list) else []
        extra: list[str | None] = []
        for entry in secondary:
            if isinstance(entry, dict):
                nested = entry.get("location")
                extra.append(nested if isinstance(nested, str) else _location_text(entry))
            else:
                extra.append(_location_text(entry))
        plain = item.get("descriptionPlain")
        if isinstance(plain, str) and plain.strip():
            desc = plain.strip()
        else:
            desc = decode_html_content(item.get("descriptionHtml"))
        hosted = str(item.get("jobUrl") or "")
        apply = str(item.get("applyUrl") or hosted)
        return NormalizedJob(
            title=str(item.get("title") or "").strip(),
            company=company.name,
            location_raw=join_locations([loc, *extra]),
            apply_url=apply,
            hosted_url=hosted,
            employment_hint=(str(item.get("employmentType")).strip() or None)
            if item.get("employmentType") is not None
            else None,
            workplace_hint=(str(item.get("workplaceType")).strip() or None)
            if item.get("workplaceType") is not None
            else None,
            description_text=desc,
            source=company.id,
            ats=self.ats,
            external_id=str(item.get("id") or ""),
            raw_json=json.dumps(item, separators=(",", ":"), ensure_ascii=False),
        )
