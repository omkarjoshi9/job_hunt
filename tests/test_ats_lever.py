from __future__ import annotations

import json
from pathlib import Path

import httpx

from jobdigest.ats.base import CompanyConfig, FetchStatus
from jobdigest.ats.lever import LeverAdapter

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ats" / "lever"
COMPANY = CompanyConfig(id="example", name="Example Corp", ats="lever", token="example", enabled=False)


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _is_lever(request: httpx.Request) -> bool:
    return "/postings/" in request.url.path and request.url.params.get("mode") == "json"


def test_two_pages_yield_three_jobs():
    def handler(request: httpx.Request) -> httpx.Response:
        if not _is_lever(request):
            return httpx.Response(500, text="unexpected")
        skip = request.url.params.get("skip", "0")
        if skip == "0":
            return httpx.Response(200, json=_load("page0.json"))
        if skip == "2":
            return httpx.Response(200, json=_load("page1.json"))
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = LeverAdapter(client, sleep=lambda _: None, page_limit=2).fetch(COMPANY)
    assert result.status is FetchStatus.OK
    assert len(result.jobs) == 3
    assert [job.external_id for job in result.jobs] == ["lev-1", "lev-2", "lev-3"]
    assert result.jobs[0].title == "Backend Intern"
    assert result.jobs[0].employment_hint == "Intern"
    assert result.jobs[0].workplace_hint == "hybrid"
    assert result.jobs[0].apply_url.endswith("/apply")
    assert result.jobs[0].posted_at == "2023-11-14T22:13:20Z"
    assert result.jobs[1].posted_at is None
    assert result.jobs[2].title == "GET Software Engineer"


def test_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_lever(request):
            return httpx.Response(200, json=_load("empty.json"))
        return httpx.Response(500, text="unexpected")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = LeverAdapter(client, sleep=lambda _: None, page_limit=2).fetch(COMPANY)
    assert result.status is FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert result.http_status == 200


def test_404_is_dead_token():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_lever(request):
            return httpx.Response(404, json=_load("not_found.json"))
        return httpx.Response(500, text="unexpected")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = LeverAdapter(client, sleep=lambda _: None).fetch(COMPANY)
    assert result.status is FetchStatus.DEAD_TOKEN
    assert result.status is not FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert result.http_status == 404


def test_partial_pagination_failure_is_incomplete_but_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("skip", "0") == "0":
            return httpx.Response(200, json=_load("page0.json"))
        return httpx.Response(404, text="gone")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = LeverAdapter(client, sleep=lambda _: None, page_limit=2).fetch(COMPANY)
    assert result.status is FetchStatus.OK
    assert result.complete is False
    assert len(result.jobs) == 2


def test_complete_true_on_full_fetch():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_load("empty.json"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = LeverAdapter(client, sleep=lambda _: None, page_limit=2).fetch(COMPANY)
    assert result.complete is True
