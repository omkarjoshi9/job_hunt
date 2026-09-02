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


class GreenhouseAdapter:
    ats = "greenhouse"

    def __init__(self, client: httpx.Client, *, sleep: Callable[[float], None]) -> None:
        self._client = client
        self._sleep = sleep

    def fetch(self, company: CompanyConfig) -> FetchResult:
        url = (
            "https://boards-api.greenhouse.io/v1/boards/"
            f"{company.token}/jobs?content=true"
        )
        try:
            response = request_get(self._client, url, sleep=self._sleep)
        except httpx.HTTPError as exc:
            return FetchResult(FetchStatus.ERROR, None, [], error=str(exc))
        terminal = classify_http_status(response.status_code)
        if terminal is not None:
            return FetchResult(terminal, response.status_code, [], error=None)
        payload = parse_json_or_none(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return FetchResult(FetchStatus.ERROR, response.status_code, [], error="invalid greenhouse json")
        raw_jobs = payload["jobs"]
        jobs = [self._map(company, item) for item in raw_jobs if isinstance(item, dict)]
        return FetchResult(empty_or_ok(len(jobs)), response.status_code, jobs)

    def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob:
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        offices = item.get("offices") if isinstance(item.get("offices"), list) else []
        office_bits = []
        for office in offices:
            if isinstance(office, dict):
                office_bits.append(office.get("location") or office.get("name"))
        company_name = str(item.get("company_name") or "").strip() or company.name
        abs_url = str(item.get("absolute_url") or "")
        return NormalizedJob(
            title=str(item.get("title") or "").strip(),
            company=company_name,
            location_raw=join_locations([loc.get("name"), *office_bits]),
            apply_url=abs_url,
            hosted_url=abs_url,
            employment_hint=None,
            workplace_hint=None,
            description_text=decode_html_content(item.get("content")),
            source=company.id,
            ats=self.ats,
            external_id=str(item.get("id") or ""),
            raw_json=json.dumps(item, separators=(",", ":"), ensure_ascii=False),
        )
