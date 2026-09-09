import json
from pathlib import Path

import httpx

from jobdigest.contacts.email import construct_email
from jobdigest.contacts.github import github_headers, github_org_recruiters, github_token
from jobdigest.contacts.pattern import email_pattern_detection

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contacts"


def _json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_construct_email_first_last():
    assert construct_email("Alice Smith", "{first}.{last}@acme.com") == "alice.smith@acme.com"
    assert construct_email("", "{first}.{last}@acme.com") is None


def test_github_token_ignores_actions_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "actions-secret")
    monkeypatch.delenv("JOBDIGEST_GITHUB_TOKEN", raising=False)
    assert github_token() == ""
    headers = github_headers()
    assert "Authorization" not in headers
    monkeypatch.setenv("JOBDIGEST_GITHUB_TOKEN", "pat-jobdigest")
    assert github_token() == "pat-jobdigest"
    assert github_headers()["Authorization"] == "Bearer pat-jobdigest"


def test_email_pattern_from_commits():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/commits" in str(request.url):
            return httpx.Response(200, json=_json("github_commits.json"))
        return httpx.Response(500, text="unexpected")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        pattern = email_pattern_detection("acme.com", client, token="pat")
    assert pattern == "{first}.{last}@acme.com"


def test_github_org_recruiters_filters_engineers():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers.get("Authorization") == "Bearer pat-jobdigest"
        path = request.url.path
        if path.endswith("/orgs/acme/members"):
            return httpx.Response(200, json=_json("github_members.json"))
        if path.endswith("/users/talent-jane"):
            return httpx.Response(200, json=_json("github_user_recruiter.json"))
        if path.endswith("/users/eng-bob"):
            return httpx.Response(200, json=_json("github_user_engineer.json"))
        return httpx.Response(404, json={"message": "no"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        found = github_org_recruiters("acme", client, token="pat-jobdigest")
    names = [c.name for c in found]
    assert names == ["Jane Talent"]
    assert found[0].source == "github"
    assert found[0].confidence == 70.0
    assert "Bob Builder" not in names


def test_github_org_403_is_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "rate limit"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        assert github_org_recruiters("acme", client, token="pat") == []
