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
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.params.get("page") is None
        if _is_gh_jobs(request):
            return httpx.Response(200, json=_load("two_jobs.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert result.complete is True
    assert calls["n"] == 1
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
    assert intern.employment_hint is None
    assert intern.workplace_hint is None
    assert intern.posted_at == "2024-06-01T00:00:00Z"
    assert result.jobs[1].posted_at is None


def test_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_gh_jobs(request):
            return httpx.Response(200, json=_load("empty.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.EMPTY_BOARD
    assert result.complete is True
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


def test_offices_parent_name_not_in_location():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_gh_jobs(request):
            return httpx.Response(200, json=_load("offices_parent_vs_leaf.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    by_id = {job.external_id: job for job in result.jobs}
    parent = by_id["579"]
    intern = by_id["581"]
    perm = by_id["572"]
    assert parent.location_raw == ""
    assert "India Locations" not in parent.location_raw
    assert "N/A" not in parent.location_raw
    assert intern.location_raw == "Bengaluru | Bengaluru, Karnataka, India"
    assert "IN-Bengaluru" not in intern.location_raw
    assert perm.location_raw == "Seattle, WA"
    assert "US-PERM" not in perm.location_raw


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


def test_meta_total_mismatch_duplicate_page_is_incomplete(caplog):
    payload = _load("two_jobs.json")
    payload["meta"]["total"] = 5
    urls: list[str] = []
    caplog.set_level("WARNING")

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if _is_gh_jobs(request):
            return httpx.Response(200, json=payload)
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert result.complete is False
    assert [job.external_id for job in result.jobs] == ["101", "102"]
    assert len(urls) == 2
    assert "page=2" in urls[1]
    assert all("page=3" not in url for url in urls)
    assert "greenhouse incomplete" in caplog.text
    assert "total=5" in caplog.text
    assert "got=2" in caplog.text


def test_meta_total_mismatch_second_page_appends():
    page1 = _load("two_jobs.json")
    page1["meta"]["total"] = 3
    page2 = _load("page2_new.json")
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if not _is_gh_jobs(request):
            return httpx.Response(500, text="unexpected")
        page = request.url.params.get("page")
        if page is None:
            return httpx.Response(200, json=page1)
        if page == "2":
            return httpx.Response(200, json=page2)
        return httpx.Response(500, text="no further pages")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert result.complete is True
    assert [job.external_id for job in result.jobs] == ["101", "102", "103"]
    assert result.jobs[2].title == "New Grad Software Engineer"
    assert len(urls) == 2


def test_page2_http_error_keeps_page1_incomplete():
    page1 = _load("two_jobs.json")
    page1["meta"]["total"] = 5

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page"):
            return httpx.Response(500, json={"error": "fail"})
        if _is_gh_jobs(request):
            return httpx.Response(200, json=page1)
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert result.complete is False
    assert [job.external_id for job in result.jobs] == ["101", "102"]


def test_max_pages_marks_incomplete():
    def handler(request: httpx.Request) -> httpx.Response:
        raw_page = request.url.params.get("page") or "1"
        jid = 200 + int(raw_page)
        body = {
            "jobs": [
                {
                    "id": jid,
                    "title": "Software Engineer Intern",
                    "absolute_url": f"https://boards.greenhouse.io/example/jobs/{jid}",
                    "location": {"name": "Pune, India"},
                    "content": "Intern role in Pune",
                    "company_name": "Example Corp",
                }
            ],
            "meta": {"total": 50},
        }
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = GreenhouseAdapter(client, sleep=lambda _: None, max_pages=3).fetch(
            COMPANY
        )
    assert result.status is FetchStatus.OK
    assert result.complete is False
    assert [job.external_id for job in result.jobs] == ["201", "202", "203"]
