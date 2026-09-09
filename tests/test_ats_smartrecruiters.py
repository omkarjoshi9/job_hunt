from __future__ import annotations

import json
from pathlib import Path

import httpx

from jobdigest.ats.base import CompanyConfig, FetchStatus
from jobdigest.ats.smartrecruiters import SmartRecruitersAdapter

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ats" / "smartrecruiters"
COMPANY = CompanyConfig(
    id="example",
    name="Example Corp",
    ats="smartrecruiters",
    token="example",
    enabled=False,
)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _is_sr_list(request: httpx.Request) -> bool:
    return request.url.path == "/v1/companies/example/postings"


def _fetch(handler, sleep=lambda _: None, **kwargs):
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        return SmartRecruitersAdapter(client, sleep=sleep, **kwargs).fetch(COMPANY)


def test_two_jobs_ok_trims_title_and_https_apply():
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if _is_sr_list(request):
            return httpx.Response(200, json=_load("two_jobs.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert result.complete is True
    assert len(result.jobs) == 2
    intern = result.jobs[0]
    assert intern.title == "Software Engineer Intern"
    assert intern.apply_url == "https://www.smartrecruiters.com/example/sr-101"
    assert intern.apply_url.startswith("https://")
    assert intern.hosted_url == intern.apply_url
    assert intern.company == "Example Corp"
    assert intern.ats == "smartrecruiters"
    assert intern.source == "example"
    assert intern.external_id == "sr-101"
    assert intern.employment_hint == "Internship"
    assert intern.workplace_hint is None
    assert intern.location_raw == "Bengaluru | Karnataka | India"
    assert intern.description_text == ""
    assert intern.posted_at == "2024-06-01T00:00:00Z"
    staff = result.jobs[1]
    assert staff.apply_url == "https://www.smartrecruiters.com/example/sr-102"
    assert staff.workplace_hint == "remote"
    assert "Austin" in staff.location_raw
    assert "USA" in staff.location_raw
    assert "Remote" in staff.location_raw
    assert "India" not in staff.location_raw
    assert staff.posted_at is None
    assert all("/postings/sr-" not in url for url in urls)
    assert all(url.rstrip("/").endswith("/postings") or "/postings?" in url for url in urls)


def test_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_sr_list(request):
            return httpx.Response(200, json=_load("empty.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.EMPTY_BOARD
    assert result.complete is True
    assert result.jobs == []
    assert result.http_status == 200


def test_404_is_dead_token_not_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_sr_list(request):
            return httpx.Response(404, json=_load("not_found.json"))
        return httpx.Response(500, text="unexpected")

    result = _fetch(handler)
    assert result.status is FetchStatus.DEAD_TOKEN
    assert result.status is not FetchStatus.EMPTY_BOARD
    assert result.jobs == []
    assert result.http_status == 404


def test_pagination_concatenates_without_dup_ids():
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if not _is_sr_list(request):
            return httpx.Response(500, text="unexpected")
        offset = request.url.params.get("offset") or "0"
        if offset == "0":
            return httpx.Response(200, json=_load("page0.json"))
        if offset == "2":
            return httpx.Response(200, json=_load("page1.json"))
        return httpx.Response(500, text="no further pages")

    result = _fetch(handler, page_limit=2)
    assert result.status is FetchStatus.OK
    assert result.complete is True
    assert [job.external_id for job in result.jobs] == ["sr-101", "sr-102", "sr-103"]
    assert result.jobs[2].title == "New Grad Software Engineer"
    assert len(urls) == 2
    assert "limit=2" in urls[0]
    assert "offset=0" in urls[0]
    assert "offset=2" in urls[1]


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


def test_max_pages_marks_incomplete():
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset") or "0")
        jid = 200 + offset
        body = {
            "offset": offset,
            "limit": 1,
            "totalFound": 50,
            "content": [
                {
                    "id": str(jid),
                    "uuid": f"uuid-{jid}",
                    "name": "Software Engineer Intern",
                    "location": {"city": "Pune", "country": "India", "remote": False},
                    "company": {"identifier": "example", "name": "Example Corp"},
                }
            ],
        }
        return httpx.Response(200, json=body)

    result = _fetch(handler, page_limit=1, max_pages=3)
    assert result.status is FetchStatus.OK
    assert result.complete is False
    assert [job.external_id for job in result.jobs] == ["200", "201", "202"]
