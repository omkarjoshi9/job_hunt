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


def _location_entry(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        joined = join_locations(
            [
                entry.get("city") if isinstance(entry.get("city"), str) else None,
                entry.get("state") if isinstance(entry.get("state"), str) else None,
                entry.get("region") if isinstance(entry.get("region"), str) else None,
                entry.get("country") if isinstance(entry.get("country"), str) else None,
                entry.get("location") if isinstance(entry.get("location"), str) else None,
                entry.get("name") if isinstance(entry.get("name"), str) else None,
            ]
        )
        return joined or None
    return None


class WorkableAdapter:
    ats = "workable"

    def __init__(self, client: httpx.Client, *, sleep: Callable[[float], None]) -> None:
        self._client = client
        self._sleep = sleep

    def fetch(self, company: CompanyConfig) -> FetchResult:
        url = (
            "https://apply.workable.com/api/v1/widget/accounts/"
            f"{company.token}?details=true"
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
            return FetchResult(FetchStatus.ERROR, response.status_code, [], error="invalid workable json")
        envelope_name = str(payload.get("name") or "").strip()
        jobs = [
            self._map(company, item, envelope_name)
            for item in payload["jobs"]
            if isinstance(item, dict)
        ]
        return FetchResult(empty_or_ok(len(jobs)), response.status_code, jobs)

    def _map(self, company: CompanyConfig, item: dict, envelope_name: str) -> NormalizedJob:
        extra = item.get("locations") if isinstance(item.get("locations"), list) else []
        location_raw = join_locations(
            [
                item.get("city") if isinstance(item.get("city"), str) else None,
                item.get("state") if isinstance(item.get("state"), str) else None,
                item.get("country") if isinstance(item.get("country"), str) else None,
                *(_location_entry(entry) for entry in extra),
            ]
        )
        shortcode = str(item.get("shortcode") or "")
        apply = str(item.get("application_url") or "").strip()
        if not apply and shortcode:
            apply = f"https://apply.workable.com/j/{shortcode}/apply"
        hosted = str(item.get("url") or item.get("shortlink") or "")
        if item.get("workplace_type") is not None:
            workplace_hint = str(item.get("workplace_type")).strip() or None
        elif item.get("telecommuting") is True:
            workplace_hint = "remote"
        else:
            workplace_hint = None
        return NormalizedJob(
            title=str(item.get("title") or "").strip(),
            company=envelope_name or company.name,
            location_raw=location_raw,
            apply_url=apply,
            hosted_url=hosted,
            employment_hint=(str(item.get("employment_type")).strip() or None)
            if item.get("employment_type") is not None
            else None,
            workplace_hint=workplace_hint,
            description_text=decode_html_content(item.get("description")),
            source=company.id,
            ats=self.ats,
            external_id=shortcode,
            raw_json=json.dumps(item, separators=(",", ":"), ensure_ascii=False),
        )
