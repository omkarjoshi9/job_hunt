from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from jobdigest.ats.base import (
    CompanyConfig,
    FetchResult,
    JobCollector,
    NormalizedJob,
    Page,
    decode_html_content,
    empty_or_ok,
    get_json_page,
    join_locations,
    posted_at_from_item,
)
from jobdigest.logutil import get_logger

_PLACEHOLDERS = frozenset({"n/a", "na"})
_DEFAULT_MAX_PAGES = 20
log = get_logger("ats.greenhouse")


def _loc_bit(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in _PLACEHOLDERS:
        return None
    return text


def _jobs_url(token: str, page: int | None = None) -> str:
    url = (
        "https://boards-api.greenhouse.io/v1/boards/"
        f"{token}/jobs?content=true"
    )
    if page is not None:
        return f"{url}&page={page}"
    return url


def _meta_total(payload: dict) -> int | None:
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    total = meta.get("total")
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    return total


def _item_id(item: dict) -> str:
    return str(item.get("id") or "").strip()


def _dict_jobs(payload: dict) -> list[dict]:
    raw = payload.get("jobs")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


class GreenhouseAdapter:
    ats = "greenhouse"

    def __init__(
        self,
        client: httpx.Client,
        *,
        sleep: Callable[[float], None],
        max_pages: int = _DEFAULT_MAX_PAGES,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._max_pages = max(1, int(max_pages))

    def fetch(self, company: CompanyConfig) -> FetchResult:
        def valid(payload: object) -> bool:
            return isinstance(payload, dict) and isinstance(payload.get("jobs"), list)

        def get(url: str) -> Page:
            return get_json_page(
                self._client, url, sleep=self._sleep, valid=valid, label="greenhouse"
            )

        first = get(_jobs_url(company.token))
        if first.failure is not None:
            return first.failure
        collector = JobCollector(_item_id)
        collector.absorb(_dict_jobs(first.payload))
        total = _meta_total(first.payload)
        last_status = first.http_status
        complete = True
        pages_fetched = 1
        page = 2
        while (
            total is not None
            and total > len(collector)
            and pages_fetched < self._max_pages
        ):
            nxt = get(_jobs_url(company.token, page=page))
            pages_fetched += 1
            if nxt.http_status is not None:
                last_status = nxt.http_status
            items = _dict_jobs(nxt.payload) if nxt.failure is None else []
            if nxt.failure is not None or not collector.new_ids(items):
                complete = False
                break
            collector.absorb(items)
            page += 1
        got = len(collector)
        if total is not None and total > got:
            complete = False
            log.warning(
                "greenhouse incomplete token=%s total=%s got=%s",
                company.token,
                total,
                got,
            )
        jobs = [self._map(company, item) for item in collector.items()]
        return FetchResult(empty_or_ok(len(jobs)), last_status, jobs, complete=complete)

    def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob:
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        offices = item.get("offices") if isinstance(item.get("offices"), list) else []
        office_bits = [
            _loc_bit(office.get("location"))
            for office in offices
            if isinstance(office, dict)
        ]
        company_name = str(item.get("company_name") or "").strip() or company.name
        abs_url = str(item.get("absolute_url") or "")
        return NormalizedJob(
            title=str(item.get("title") or "").strip(),
            company=company_name,
            location_raw=join_locations([_loc_bit(loc.get("name")), *office_bits]),
            apply_url=abs_url,
            hosted_url=abs_url,
            employment_hint=None,
            workplace_hint=None,
            description_text=decode_html_content(item.get("content")),
            source=company.id,
            ats=self.ats,
            external_id=str(item.get("id") or ""),
            raw_json=json.dumps(item, separators=(",", ":"), ensure_ascii=False),
            posted_at=posted_at_from_item(item, "updated_at"),
        )
