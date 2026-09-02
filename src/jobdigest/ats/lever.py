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


class LeverAdapter:
    ats = "lever"

    def __init__(
        self,
        client: httpx.Client,
        *,
        sleep: Callable[[float], None],
        page_limit: int = 100,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._page_limit = page_limit

    def fetch(self, company: CompanyConfig) -> FetchResult:
        skip = 0
        collected: list[dict] = []
        last_status = None
        while True:
            url = (
                f"https://api.lever.co/v0/postings/{company.token}"
                f"?mode=json&limit={self._page_limit}&skip={skip}"
            )
            try:
                response = request_get(self._client, url, sleep=self._sleep)
            except httpx.HTTPError as exc:
                return FetchResult(FetchStatus.ERROR, None, [], error=str(exc))
            last_status = response.status_code
            terminal = classify_http_status(response.status_code)
            if terminal is not None:
                return FetchResult(terminal, response.status_code, [], error=None)
            payload = parse_json_or_none(response)
            if not isinstance(payload, list):
                return FetchResult(
                    FetchStatus.ERROR, response.status_code, [], error="invalid lever json"
                )
            page = [item for item in payload if isinstance(item, dict)]
            collected.extend(page)
            if len(payload) < self._page_limit:
                break
            skip += len(payload)
        jobs = [self._map(company, item) for item in collected]
        return FetchResult(empty_or_ok(len(jobs)), last_status, jobs)

    def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob:
        cats = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        extra = cats.get("allLocations") if isinstance(cats.get("allLocations"), list) else []
        hosted = str(item.get("hostedUrl") or "")
        apply = str(item.get("applyUrl") or hosted)
        plain = item.get("descriptionPlain")
        if isinstance(plain, str) and plain.strip():
            desc = plain.strip()
        else:
            chunks = [
                decode_html_content(item.get("description")),
                str(item.get("openingPlain") or "").strip(),
                str(item.get("additionalPlain") or "").strip(),
            ]
            desc = join_locations(chunks).replace(" | ", " ")
        return NormalizedJob(
            title=str(item.get("text") or "").strip(),
            company=company.name,
            location_raw=join_locations([cats.get("location"), *[str(x) for x in extra]]),
            apply_url=apply,
            hosted_url=hosted,
            employment_hint=(str(cats.get("commitment")).strip() or None)
            if cats.get("commitment") is not None
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
