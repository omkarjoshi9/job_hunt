from __future__ import annotations

import json
from pathlib import Path

import httpx

from jobdigest.ats.base import CompanyConfig, FetchStatus
from jobdigest.ats.workable import WorkableAdapter

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ats" / "workable"
COMPANY = CompanyConfig(id="example", name="Example Corp", ats="workable", token="example", enabled=False)


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _is_workable(request: httpx.Request) -> bool:
    return "/api/v1/widget/accounts/" in request.url.path and request.url.params.get("details") == "true"


def _fetch(handler) -> object:
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        return WorkableAdapter(client, sleep=lambda _: None).fetch(COMPANY)


def test_maps_shortcode_and_two_jobs():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_workable(request):
            return httpx.Response(200, json=_load("two_jobs.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert len(result.jobs) == 2
    intern = result.jobs[0]
    assert intern.external_id == "INT001"
    assert intern.title == "Software Intern"
    assert intern.company == "Example Corp"
    assert intern.apply_url == "https://apply.workable.com/j/INT001/apply"
    assert intern.hosted_url == "https://apply.workable.com/example/j/INT001/"
    assert intern.employment_hint == "Internship"
    assert intern.workplace_hint == "on_site"
    assert "Bengaluru" in intern.location_raw
    assert "Bengaluru" in intern.description_text
    assert "<p>" not in intern.description_text
    assert result.jobs[1].external_id == "STAFF1"


def test_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_workable(request):
            return httpx.Response(200, json=_load("empty.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert result.http_status == 200


def test_404_is_dead_token():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_workable(request):
            return httpx.Response(404, json=_load("not_found.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.DEAD_TOKEN
    assert result.status is not FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert result.http_status == 404
