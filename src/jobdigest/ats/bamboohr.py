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


def _list_url(token: str) -> str:
    return f"https://{token}.bamboohr.com/careers/list"


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _str_field(blob: dict, key: str) -> str | None:
    raw = blob.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _item_id(item: dict) -> str:
    return str(item.get("id") or "").strip()


def _title(item: dict) -> str:
    return str(item.get("jobOpeningName") or item.get("title") or "").strip()


def _employment_hint(item: dict) -> str | None:
    for key in ("employmentStatusLabel", "employmentType"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            label = str(raw.get("label") or raw.get("name") or raw.get("id") or "").strip()
            if label:
                return label
    return None


def _location(item: dict) -> tuple[str, str | None]:
    loc = _as_dict(item.get("location"))
    ats = _as_dict(item.get("atsLocation"))
    remote = item.get("isRemote") is True
    location_raw = join_locations(
        [
            _str_field(loc, "city"),
            _str_field(loc, "state"),
            _str_field(ats, "city"),
            _str_field(ats, "state"),
            _str_field(ats, "province"),
            _str_field(ats, "country"),
            "Remote" if remote else None,
        ]
    )
    return location_raw, ("remote" if remote else None)


def _description(item: dict) -> str:
    for key in (
        "description",
        "descriptionText",
        "jobDescription",
        "jobOpeningShareText",
    ):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _apply_url(company: CompanyConfig, item: dict) -> str:
    jid = _item_id(item)
    token = (company.token or "").strip()
    if token and jid:
        return f"https://{token}.bamboohr.com/careers/{jid}"
    return ""


class BambooHRAdapter:
    ats = "bamboohr"

    def __init__(
        self,
        client: httpx.Client,
        *,
        sleep: Callable[[float], None],
    ) -> None:
        self._client = client
        self._sleep = sleep

    def fetch(self, company: CompanyConfig) -> FetchResult:
        url = _list_url(company.token)
        try:
            response = request_get(self._client, url, sleep=self._sleep)
        except httpx.HTTPError as exc:
            return FetchResult(FetchStatus.ERROR, None, [], error=str(exc))
        terminal = classify_http_status(response.status_code)
        if terminal is not None:
            return FetchResult(terminal, response.status_code, [], error=None)
        payload = parse_json_or_none(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
            return FetchResult(
                FetchStatus.ERROR,
                response.status_code,
                [],
                error="invalid bamboohr json",
            )
        jobs: list[NormalizedJob] = []
        for item in payload["result"]:
            if not isinstance(item, dict):
                continue
            mapped = self._map(company, item)
            if mapped is not None:
                jobs.append(mapped)
        return FetchResult(empty_or_ok(len(jobs)), response.status_code, jobs, complete=True)

    def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob | None:
        apply = _apply_url(company, item)
        external_id = _item_id(item)
        if not apply and not external_id:
            return None
        location_raw, workplace = _location(item)
        return NormalizedJob(
            title=_title(item),
            company=company.name,
            location_raw=location_raw,
            apply_url=apply,
            hosted_url=apply,
            employment_hint=_employment_hint(item),
            workplace_hint=workplace,
            description_text=_description(item),
            source=company.id,
            ats=self.ats,
            external_id=external_id,
            raw_json=json.dumps(item, separators=(",", ":"), ensure_ascii=False),
            posted_at=posted_at_from_item(item, "datePosted", "postedDate"),
        )
