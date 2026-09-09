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
    empty_or_ok,
    join_locations,
    posted_at_from_item,
)
from jobdigest.ats.http_client import parse_json_or_none, request_get
from jobdigest.logutil import get_logger

_DEFAULT_PAGE_LIMIT = 100
_DEFAULT_MAX_PAGES = 20
log = get_logger("ats.smartrecruiters")


def _postings_url(token: str, *, limit: int, offset: int) -> str:
    return (
        "https://api.smartrecruiters.com/v1/companies/"
        f"{token}/postings?limit={limit}&offset={offset}"
    )


def _item_id(item: dict) -> str:
    return str(item.get("id") or item.get("uuid") or "").strip()


def _total_found(payload: dict) -> int | None:
    total = payload.get("totalFound")
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    return total


def _content(payload: dict) -> list[dict]:
    raw = payload.get("content")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _http_url(value: object) -> str | None:
    text = str(value or "").strip()
    low = text.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return text
    return None


def _company_blob(item: dict) -> dict:
    raw = item.get("company")
    return raw if isinstance(raw, dict) else {}


def _company_identifier(company: CompanyConfig, item: dict) -> str:
    ident = str(_company_blob(item).get("identifier") or "").strip()
    if ident:
        return ident
    return (company.token or "").strip()


def _company_name(company: CompanyConfig, item: dict) -> str:
    name = str(_company_blob(item).get("name") or "").strip()
    return name or company.name


def _employment_hint(item: dict) -> str | None:
    raw = item.get("typeOfEmployment")
    if isinstance(raw, dict):
        label = str(raw.get("label") or "").strip()
        if label:
            return label
        eid = str(raw.get("id") or "").strip()
        return eid or None
    if isinstance(raw, str):
        return raw.strip() or None
    return None


def _location_and_workplace(item: dict) -> tuple[str, str | None]:
    loc = item.get("location") if isinstance(item.get("location"), dict) else {}
    remote = loc.get("remote") is True
    location_raw = join_locations(
        [
            loc.get("city") if isinstance(loc.get("city"), str) else None,
            loc.get("region") if isinstance(loc.get("region"), str) else None,
            loc.get("country") if isinstance(loc.get("country"), str) else None,
            "Remote" if remote else None,
        ]
    )
    return location_raw, ("remote" if remote else None)


def _apply_url(company: CompanyConfig, item: dict) -> str:
    apply = _http_url(item.get("applyUrl"))
    if apply:
        return apply
    identifier = _company_identifier(company, item)
    jid = str(item.get("id") or "").strip()
    if identifier and jid:
        return f"https://www.smartrecruiters.com/{identifier}/{jid}"
    uuid = str(item.get("uuid") or "").strip()
    if identifier and uuid:
        return f"https://www.smartrecruiters.com/{identifier}/{uuid}"
    ref = _http_url(item.get("ref"))
    if ref and "api.smartrecruiters.com" not in ref.lower():
        return ref
    return ""


class SmartRecruitersAdapter:
    ats = "smartrecruiters"

    def __init__(
        self,
        client: httpx.Client,
        *,
        sleep: Callable[[float], None],
        page_limit: int = _DEFAULT_PAGE_LIMIT,
        max_pages: int = _DEFAULT_MAX_PAGES,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._page_limit = min(100, max(1, int(page_limit)))
        self._max_pages = max(1, int(max_pages))

    def fetch(self, company: CompanyConfig) -> FetchResult:
        offset = 0
        limit = self._page_limit
        collected: dict[str, dict] = {}
        extras: list[dict] = []
        last_status: int | None = None
        complete = True
        pages_fetched = 0
        total: int | None = None

        def absorb(items: list[dict]) -> None:
            for item in items:
                jid = _item_id(item)
                if not jid:
                    extras.append(item)
                    continue
                if jid in collected:
                    continue
                collected[jid] = item

        while pages_fetched < self._max_pages:
            url = _postings_url(company.token, limit=limit, offset=offset)
            try:
                response = request_get(self._client, url, sleep=self._sleep)
            except httpx.HTTPError as exc:
                if pages_fetched == 0:
                    return FetchResult(FetchStatus.ERROR, None, [], error=str(exc))
                complete = False
                break
            last_status = response.status_code
            terminal = classify_http_status(response.status_code)
            if terminal is not None:
                if pages_fetched == 0:
                    return FetchResult(terminal, response.status_code, [], error=None)
                complete = False
                break
            payload = parse_json_or_none(response)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("content"), list
            ):
                if pages_fetched == 0:
                    return FetchResult(
                        FetchStatus.ERROR,
                        response.status_code,
                        [],
                        error="invalid smartrecruiters json",
                    )
                complete = False
                break
            if total is None:
                total = _total_found(payload)
            items = _content(payload)
            absorb(items)
            pages_fetched += 1
            n = len(items)
            if n == 0:
                break
            if total is not None and offset + n >= total:
                break
            if n < limit:
                break
            offset += n

        got = len(collected)
        if total is not None and total > got:
            complete = False
            log.warning(
                "smartrecruiters incomplete token=%s total=%s got=%s",
                company.token,
                total,
                got,
            )

        jobs: list[NormalizedJob] = []
        for item in list(collected.values()) + extras:
            mapped = self._map(company, item)
            if mapped is not None:
                jobs.append(mapped)
        return FetchResult(
            empty_or_ok(len(jobs)), last_status, jobs, complete=complete
        )

    def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob | None:
        apply = _apply_url(company, item)
        external_id = _item_id(item)
        if not apply and not external_id:
            return None
        location_raw, workplace = _location_and_workplace(item)
        return NormalizedJob(
            title=str(item.get("name") or "").strip(),
            company=_company_name(company, item),
            location_raw=location_raw,
            apply_url=apply,
            hosted_url=apply,
            employment_hint=_employment_hint(item),
            workplace_hint=workplace,
            description_text="",
            source=company.id,
            ats=self.ats,
            external_id=external_id,
            raw_json=json.dumps(item, separators=(",", ":"), ensure_ascii=False),
            posted_at=posted_at_from_item(item, "releasedDate", "createdOn"),
        )
