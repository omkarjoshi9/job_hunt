"""Tests for AshbyAdapter.

The Ashby posting-api /job-board/{token} endpoint returns all listed jobs in a
single HTTP response.  It does not support pagination (no page / offset / cursor
parameters).  These tests verify that behaviour.
"""
from __future__ import annotations

import json

import httpx

from jobdigest.ats.ashby import AshbyAdapter
from jobdigest.ats.base import CompanyConfig, FetchStatus

COMPANY = CompanyConfig(
    id="example",
    name="Example Corp",
    ats="ashby",
    token="example-token",
    enabled=False,
)

_TWO_JOBS = {
    "jobs": [
        {
            "id": "abc-001",
            "title": "Software Engineer Intern",
            "isListed": True,
            "location": "Bengaluru",
            "jobUrl": "https://jobs.ashbyhq.com/example/abc-001",
            "applyUrl": "https://jobs.ashbyhq.com/example/abc-001/application",
            "employmentType": "FullTime",
            "workplaceType": "Remote",
            "descriptionPlain": "An intern role.",
            "publishedAt": "2024-06-01T00:00:00Z",
        },
        {
            "id": "abc-002",
            "title": "Backend Intern",
            "isListed": True,
            "location": "Pune",
            "jobUrl": "https://jobs.ashbyhq.com/example/abc-002",
            "applyUrl": "https://jobs.ashbyhq.com/example/abc-002/application",
            "employmentType": "FullTime",
            "workplaceType": "OnSite",
            "descriptionPlain": "Backend role.",
        },
    ]
}


def _fetch(handler, sleep=lambda _: None):
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        return AshbyAdapter(client, sleep=sleep).fetch(COMPANY)


# ---------------------------------------------------------------------------
# Pagination contract: the adapter MUST NOT send pagination parameters
# ---------------------------------------------------------------------------

def test_no_pagination_params_sent():
    """Ashby returns all jobs in one call; no page/offset/cursor must be sent."""
    seen_params: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        return httpx.Response(200, json=_TWO_JOBS)

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert len(seen_params) == 1, "adapter must make exactly one HTTP request"
    params = seen_params[0]
    for forbidden in ("page", "offset", "cursor", "after", "limit", "skip"):
        assert forbidden not in params, f"unexpected pagination param '{forbidden}' sent to Ashby"


# ---------------------------------------------------------------------------
# Basic round-trip
# ---------------------------------------------------------------------------

def test_two_jobs_ok():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert f"/posting-api/job-board/{COMPANY.token}" in request.url.path
        return httpx.Response(200, json=_TWO_JOBS)

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert calls["n"] == 1
    assert len(result.jobs) == 2
    assert result.jobs[0].title == "Software Engineer Intern"
    assert result.jobs[1].title == "Backend Intern"
    assert result.jobs[0].ats == "ashby"
    assert result.jobs[0].external_id == "abc-001"
    assert result.jobs[0].location_raw == "Bengaluru"
    assert result.jobs[0].employment_hint == "FullTime"
    assert result.jobs[0].workplace_hint == "Remote"
    assert result.jobs[0].posted_at == "2024-06-01T00:00:00Z"
    assert result.jobs[1].posted_at is None


def test_unlisted_job_is_excluded():
    payload = {
        "jobs": [
            {**_TWO_JOBS["jobs"][0]},
            {**_TWO_JOBS["jobs"][1], "isListed": False},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert len(result.jobs) == 1
    assert result.jobs[0].title == "Software Engineer Intern"


def test_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": []})

    result = _fetch(handler)
    assert result.status is FetchStatus.EMPTY_BOARD
    assert result.jobs == []


def test_404_is_dead_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    result = _fetch(handler)
    assert result.status is FetchStatus.DEAD_TOKEN
    assert result.jobs == []


def test_invalid_json_structure_is_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"postings": []})

    result = _fetch(handler)
    assert result.status is FetchStatus.ERROR
    assert result.jobs == []
