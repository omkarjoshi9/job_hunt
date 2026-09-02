from __future__ import annotations

import json
from pathlib import Path

import httpx

from jobdigest.ats.base import CompanyConfig, FetchStatus
from jobdigest.ats.greenhouse import GreenhouseAdapter

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ats" / "greenhouse"
COMPANY = CompanyConfig(id="example", name="Example Corp", ats="greenhouse", token="example", enabled=False)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _fetch(handler, sleep=lambda _: None) -> httpx.Response | object:
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        return GreenhouseAdapter(client, sleep=sleep).fetch(COMPANY)


def _is_gh_jobs(request: httpx.Request) -> bool:
    return request.url.path.endswith("/jobs") and request.url.params.get("content") == "true"


def test_two_jobs_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_gh_jobs(request):
            return httpx.Response(200, json=_load("two_jobs.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert len(result.jobs) == 2
    intern = result.jobs[0]
    assert intern.title == "Software Engineer Intern"
    assert intern.apply_url == "https://boards.greenhouse.io/example/jobs/101"
    assert intern.hosted_url == intern.apply_url
    assert "Bangalore" in intern.description_text
    assert "<p>" not in intern.description_text
    assert intern.company == "Example Corp"
    assert intern.ats == "greenhouse"
    assert intern.source == "example"
    assert intern.external_id == "101"


def test_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_gh_jobs(request):
            return httpx.Response(200, json=_load("empty.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert result.http_status == 200


def test_404_is_dead_token_not_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_gh_jobs(request):
            return httpx.Response(404, json=_load("not_found.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.DEAD_TOKEN
    assert result.status is not FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert result.http_status == 404


def test_double_encoded_html_description():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_gh_jobs(request):
            return httpx.Response(200, json=_load("encoded_html.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    text = result.jobs[0].description_text
    assert text == "Bengaluru intern"
    assert "&lt;" not in text
    assert "<p>" not in text


def test_429_then_200_empty_calls_sleep():
    slept: list[float] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json=_load("empty.json"))

    result = _fetch(handler, sleep=slept.append)
    assert result.status is FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert slept


def test_429_after_retry_is_error():
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"}, json={})

    result = _fetch(handler, sleep=slept.append)
    assert result.status is FetchStatus.ERROR
    assert result.jobs == []
    assert result.http_status == 429
    assert slept


def test_403_is_error_not_dead_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    result = _fetch(handler)
    assert result.status is FetchStatus.ERROR
    assert result.status is not FetchStatus.DEAD_TOKEN
    assert result.http_status == 403


def test_200_html_challenge_is_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html>challenge</html>",
        )

    result = _fetch(handler)
    assert result.status is FetchStatus.ERROR
    assert result.jobs == []
