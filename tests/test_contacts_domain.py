import json
from pathlib import Path

import httpx

from jobdigest.ats.http_client import USER_AGENT
from jobdigest.contacts.domain import domain_inference

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contacts"


def _handler_clearbit(request: httpx.Request) -> httpx.Response:
    assert USER_AGENT in (request.headers.get("user-agent") or "")
    if "autocomplete.clearbit.com" in str(request.url):
        payload = json.loads((FIXTURES / "clearbit_hit.json").read_text(encoding="utf-8"))
        return httpx.Response(200, json=payload)
    return httpx.Response(500, text="unexpected")


def test_domain_clearbit_hit():
    transport = httpx.MockTransport(_handler_clearbit)
    with httpx.Client(transport=transport) as client:
        assert domain_inference("Acme Inc", client) == "acme.com"


def test_domain_probe_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        if "autocomplete.clearbit.com" in str(request.url):
            return httpx.Response(
                200,
                json=json.loads((FIXTURES / "clearbit_empty.json").read_text(encoding="utf-8")),
            )
        host = request.url.host or ""
        if host == "acme.com":
            return httpx.Response(200, text="ok")
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        assert domain_inference("Acme", client) == "acme.com"


def test_domain_empty_company():
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    with httpx.Client(transport=transport) as client:
        assert domain_inference("  ", client) is None
