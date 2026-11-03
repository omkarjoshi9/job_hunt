from __future__ import annotations

import json
from collections.abc import Callable
import httpx

from jobdigest.ats.base import (
    CompanyConfig,
    FetchResult,
    NormalizedJob,
    decode_html_content,
    empty_or_ok,
    get_json_page,
    join_locations,
    parse_posted_at,
)


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
        complete = True
        while True:
            url = (
                f"https://api.lever.co/v0/postings/{company.token}"
                f"?mode=json&limit={self._page_limit}&skip={skip}"
            )
            pg = get_json_page(
                self._client,
                url,
                sleep=self._sleep,
                valid=lambda p: isinstance(p, list),
                label="lever",
            )
            if pg.http_status is not None:
                last_status = pg.http_status
            if pg.failure is not None:
                if not collected:
                    return pg.failure
                complete = False
                break
            payload = pg.payload
            collected.extend(item for item in payload if isinstance(item, dict))
            if len(payload) < self._page_limit:
                break
            skip += len(payload)
        jobs = [self._map(company, item) for item in collected]
        return FetchResult(empty_or_ok(len(jobs)), last_status, jobs, complete=complete)

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
            posted_at=parse_posted_at(item.get("createdAt")),
        )
