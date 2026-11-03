from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from jobdigest.ats.http_client import request_get


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _run(handler):
    sleeps: list[float] = []
    with _client(handler) as client:
        try:
            resp = request_get(client, "https://x.test/a", sleep=sleeps.append)
        except httpx.HTTPError as exc:
            return exc, sleeps
    return resp, sleeps


def test_ok_no_retry():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(200, json={})

    resp, sleeps = _run(h)
    assert resp.status_code == 200 and len(calls) == 1
    assert len(sleeps) == 1  # jitter only


def test_429_retried_once_with_retry_after_seconds():
    seq = [httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200)]
    resp, sleeps = _run(lambda req: seq.pop(0))
    assert resp.status_code == 200
    assert sleeps[1] == 3.0


def test_429_only_once():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "1"})

    resp, _ = _run(h)
    assert resp.status_code == 429 and len(calls) == 2


def test_retry_after_http_date():
    when = datetime.now(timezone.utc) + timedelta(seconds=10)
    seq = [
        httpx.Response(429, headers={"Retry-After": format_datetime(when, usegmt=True)}),
        httpx.Response(200),
    ]
    _, sleeps = _run(lambda req: seq.pop(0))
    assert 5.0 <= sleeps[1] <= 10.0


def test_retry_after_garbage_and_cap():
    seq = [httpx.Response(429, headers={"Retry-After": "soon"}), httpx.Response(200)]
    _, sleeps = _run(lambda req: seq.pop(0))
    assert sleeps[1] == 2.0
    seq = [httpx.Response(429, headers={"Retry-After": "999"}), httpx.Response(200)]
    _, sleeps = _run(lambda req: seq.pop(0))
    assert sleeps[1] == 30.0


def test_5xx_retried_then_success():
    seq = [httpx.Response(503), httpx.Response(502), httpx.Response(200)]
    resp, sleeps = _run(lambda req: seq.pop(0))
    assert resp.status_code == 200
    assert sleeps[1:] == [0.5, 1.0]


def test_5xx_gives_up_after_two_retries():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(500)

    resp, _ = _run(h)
    assert resp.status_code == 500 and len(calls) == 3


def test_4xx_not_retried():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(404)

    resp, _ = _run(h)
    assert resp.status_code == 404 and len(calls) == 1


def test_timeout_retried_then_success():
    calls = []

    def h(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200)

    resp, _ = _run(h)
    assert resp.status_code == 200 and len(calls) == 2


def test_timeout_raises_after_retries():
    calls = []

    def h(req):
        calls.append(1)
        raise httpx.ConnectTimeout("slow", request=req)

    exc, _ = _run(h)
    assert isinstance(exc, httpx.TimeoutException) and len(calls) == 3


def test_non_timeout_error_not_retried():
    calls = []

    def h(req):
        calls.append(1)
        raise httpx.ConnectError("nope", request=req)

    exc, _ = _run(h)
    assert isinstance(exc, httpx.ConnectError) and len(calls) == 1
