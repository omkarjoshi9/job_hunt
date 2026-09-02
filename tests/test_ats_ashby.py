from __future__ import annotations

import json
from pathlib import Path

import httpx

from jobdigest.ats.base import CompanyConfig, FetchStatus
from jobdigest.ats.ashby import AshbyAdapter

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ats" / "ashby"
COMPANY = CompanyConfig(id="example", name="Example Corp", ats="ashby", token="example", enabled=False)


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _is_ashby(request: httpx.Request) -> bool:
    return "/posting-api/job-board/" in request.url.path


def _fetch(handler) -> object:
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        return AshbyAdapter(client, sleep=lambda _: None).fetch(COMPANY)


def test_drops_unlisted_and_trims_title():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_ashby(request):
            assert request.url.path.endswith("/example")
            return httpx.Response(200, json=_load("two_jobs.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.title == "Software Engineer Intern"
    assert not job.title.startswith(" ")
    assert job.external_id == "ash-1"
    assert "Bengaluru" in job.location_raw
    assert job.hosted_url == "https://jobs.ashbyhq.com/example/ash-1"
    assert job.apply_url == "https://jobs.ashbyhq.com/example/ash-1/application"
    assert job.employment_hint == "Intern"
    assert job.workplace_hint == "OnSite"
    assert "ash-2" not in {j.external_id for j in result.jobs}


def test_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_ashby(request):
            return httpx.Response(200, json=_load("empty.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert result.http_status == 200


def test_404_is_dead_token():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_ashby(request):
            return httpx.Response(404, json=_load("not_found.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.DEAD_TOKEN
    assert result.status is not FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert result.http_status == 404
