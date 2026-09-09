from __future__ import annotations

import json
from pathlib import Path

import httpx

from jobdigest.ats.bamboohr import BambooHRAdapter
from jobdigest.ats.base import CompanyConfig, FetchStatus

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ats" / "bamboohr"
COMPANY = CompanyConfig(
    id="example",
    name="Example Corp",
    ats="bamboohr",
    token="example",
    enabled=False,
)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _is_list(request: httpx.Request) -> bool:
    return request.url.host == "example.bamboohr.com" and request.url.path == "/careers/list"


def _fetch(handler, sleep=lambda _: None):
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        return BambooHRAdapter(client, sleep=sleep).fetch(COMPANY)


def test_two_jobs_ok_trims_title_and_https_apply():
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if _is_list(request):
            return httpx.Response(200, json=_load("two_jobs.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert result.complete is True
    assert len(result.jobs) == 2
    intern = result.jobs[0]
    assert intern.title == "Software Engineer Intern"
    assert intern.apply_url == "https://example.bamboohr.com/careers/101"
    assert intern.apply_url.startswith("https://")
    assert intern.hosted_url == intern.apply_url
    assert intern.company == "Example Corp"
    assert intern.ats == "bamboohr"
    assert intern.source == "example"
    assert intern.external_id == "101"
    assert intern.employment_hint == "Internship"
    assert intern.workplace_hint is None
    assert "Bengaluru" in intern.location_raw
    assert "Karnataka" in intern.location_raw
    assert "India" in intern.location_raw
    assert intern.description_text == ""
    assert intern.posted_at == "2024-06-01T00:00:00Z"
    staff = result.jobs[1]
    assert staff.apply_url == "https://example.bamboohr.com/careers/102"
    assert staff.workplace_hint == "remote"
    assert "Austin" in staff.location_raw
    assert "USA" in staff.location_raw
    assert "Remote" in staff.location_raw
    assert "India" not in staff.location_raw
    assert staff.description_text == ""
    assert staff.posted_at is None
    assert len(urls) == 1
    assert all("/careers/" in url and url.rstrip("/").endswith("/list") for url in urls)
    assert all("/detail" not in url for url in urls)


def test_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_list(request):
            return httpx.Response(200, json=_load("empty.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.EMPTY_BOARD
    assert result.complete is True
    assert result.jobs == []
    assert result.http_status == 200


def test_404_is_dead_token_not_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_list(request):
            return httpx.Response(404, json=_load("not_found.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.DEAD_TOKEN
    assert result.status is not FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert result.http_status == 404


def test_invalid_json_is_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            text="{not-json",
        )

    result = _fetch(handler)
    assert result.status is FetchStatus.ERROR
    assert result.jobs == []


def test_200_html_login_is_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html>login</html>",
        )

    result = _fetch(handler)
    assert result.status is FetchStatus.ERROR
    assert result.jobs == []


def test_single_get_no_pagination_params():
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if _is_list(request):
            return httpx.Response(200, json=_load("two_jobs.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert result.complete is True
    assert len(urls) == 1
    assert "page=" not in urls[0]
    assert "offset=" not in urls[0]
    assert "limit=" not in urls[0]
    assert "skip=" not in urls[0]
    assert "cursor=" not in urls[0]
    assert "/careers/list" in urls[0]
