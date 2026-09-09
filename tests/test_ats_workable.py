"""Tests for WorkableAdapter.

The Workable widget /api/v1/widget/accounts/{token}?details=true endpoint
returns all active jobs in a single HTTP response.  It does not support
pagination (no page / offset / cursor parameters).  These tests verify that
behaviour.
"""
from __future__ import annotations

import httpx

from jobdigest.ats.base import CompanyConfig, FetchStatus
from jobdigest.ats.workable import WorkableAdapter

COMPANY = CompanyConfig(
    id="example",
    name="Example Corp",
    ats="workable",
    token="example-token",
    enabled=False,
)

_TWO_JOBS = {
    "name": "Example Corp",
    "jobs": [
        {
            "shortcode": "INTERN1",
            "title": "Software Engineer Intern",
            "city": "Bengaluru",
            "country": "India",
            "state": None,
            "application_url": "https://apply.workable.com/example/j/INTERN1/apply",
            "url": "https://apply.workable.com/example/j/INTERN1",
            "employment_type": "full-time",
            "workplace_type": "remote",
            "description": "<p>An intern role.</p>",
            "created_at": "2024-06-01T00:00:00Z",
        },
        {
            "shortcode": "INTERN2",
            "title": "Backend Intern",
            "city": "Pune",
            "country": "India",
            "state": None,
            "application_url": "https://apply.workable.com/example/j/INTERN2/apply",
            "url": "https://apply.workable.com/example/j/INTERN2",
            "employment_type": "full-time",
            "workplace_type": "on-site",
            "description": "<p>Backend role.</p>",
        },
    ],
}


def _fetch(handler, sleep=lambda _: None):
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        return WorkableAdapter(client, sleep=sleep).fetch(COMPANY)


# ---------------------------------------------------------------------------
# Pagination contract: the adapter MUST NOT send pagination parameters
# ---------------------------------------------------------------------------

def test_no_pagination_params_sent():
    """Workable returns all jobs in one call; no page/offset/cursor must be sent."""
    seen_params: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        return httpx.Response(200, json=_TWO_JOBS)

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert len(seen_params) == 1, "adapter must make exactly one HTTP request"
    params = seen_params[0]
    # Only `details=true` is expected; no pagination keys
    for forbidden in ("page", "offset", "cursor", "after", "limit", "skip"):
        assert forbidden not in params, (
            f"unexpected pagination param '{forbidden}' sent to Workable"
        )
    assert params.get("details") == "true"


# ---------------------------------------------------------------------------
# Basic round-trip
# ---------------------------------------------------------------------------

def test_two_jobs_ok():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert f"/api/v1/widget/accounts/{COMPANY.token}" in request.url.path
        return httpx.Response(200, json=_TWO_JOBS)

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert calls["n"] == 1
    assert len(result.jobs) == 2
    assert result.jobs[0].title == "Software Engineer Intern"
    assert result.jobs[1].title == "Backend Intern"
    assert result.jobs[0].ats == "workable"
    assert result.jobs[0].external_id == "INTERN1"
    assert result.jobs[0].workplace_hint == "remote"
    assert result.jobs[0].employment_hint == "full-time"
    assert result.jobs[0].posted_at == "2024-06-01T00:00:00Z"
    assert result.jobs[1].posted_at is None


def test_company_name_taken_from_envelope():
    """The `name` field in the envelope overrides the CompanyConfig name."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_TWO_JOBS)

    result = _fetch(handler)
    assert result.jobs[0].company == "Example Corp"


def test_empty_board():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "Example Corp", "jobs": []})

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


def test_telecommuting_true_falls_back_to_remote_hint():
    """When workplace_type is absent but telecommuting is True, hint is 'remote'."""
    payload = {
        "name": "Example Corp",
        "jobs": [
            {
                "shortcode": "TC1",
                "title": "Remote Role",
                "telecommuting": True,
                "application_url": "https://apply.workable.com/example/j/TC1/apply",
                "url": "https://apply.workable.com/example/j/TC1",
                "description": "Remote role.",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    result = _fetch(handler)
    assert result.status is FetchStatus.OK
    assert result.jobs[0].workplace_hint == "remote"
