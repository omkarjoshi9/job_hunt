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
    posted_at_from_item,
)
from jobdigest.ats.http_client import parse_json_or_none, request_get
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
        url = _jobs_url(company.token)
        try:
            response = request_get(self._client, url, sleep=self._sleep)
        except httpx.HTTPError as exc:
            return FetchResult(FetchStatus.ERROR, None, [], error=str(exc))
        terminal = classify_http_status(response.status_code)
        if terminal is not None:
            return FetchResult(terminal, response.status_code, [], error=None)
        payload = parse_json_or_none(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return FetchResult(
                FetchStatus.ERROR, response.status_code, [], error="invalid greenhouse json"
            )
        collected: dict[str, dict] = {}
        extras: list[dict] = []

        def absorb(items: list[dict]) -> None:
            for item in items:
                jid = _item_id(item)
                if not jid:
                    extras.append(item)
                    continue
                if jid in collected:
                    continue
                collected[jid] = item

        first_items = _dict_jobs(payload)
        absorb(first_items)
        total = _meta_total(payload)
        last_status = response.status_code
        complete = True
        pages_fetched = 1
        page = 2
        while (
            total is not None
            and total > len(collected)
            and pages_fetched < self._max_pages
        ):
            extra_url = _jobs_url(company.token, page=page)
            try:
                response = request_get(self._client, extra_url, sleep=self._sleep)
            except httpx.HTTPError:
                complete = False
                break
            last_status = response.status_code
            pages_fetched += 1
            if classify_http_status(response.status_code) is not None:
                complete = False
                break
            extra_payload = parse_json_or_none(response)
            if not isinstance(extra_payload, dict) or not isinstance(
                extra_payload.get("jobs"), list
            ):
                complete = False
                break
            items = _dict_jobs(extra_payload)
            page_ids = {_item_id(item) for item in items if _item_id(item)}
            seen = set(collected)
            if not page_ids or page_ids <= seen:
                complete = False
                break
            absorb(items)
            page += 1
        got = len(collected)
        if total is not None and total > got:
            complete = False
            log.warning(
                "greenhouse incomplete token=%s total=%s got=%s",
                company.token,
                total,
                got,
            )
        jobs = [
            self._map(company, item) for item in list(collected.values()) + extras
        ]
        return FetchResult(
            empty_or_ok(len(jobs)), last_status, jobs, complete=complete
        )

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
