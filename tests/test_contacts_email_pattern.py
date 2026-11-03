import httpx

from jobdigest.contacts.email import construct_email
from jobdigest.contacts.pattern import choose_pattern, email_pattern_detection

FALLBACK = "{first}.{last}@acme.com"


def test_construct_email_strips_diacritics_and_punctuation():
    assert construct_email("José Müller", "{first}.{last}@acme.com") == "jose.muller@acme.com"
    assert construct_email("Shaun O'Brien", "{first}.{last}@acme.com") == "shaun.obrien@acme.com"
    assert construct_email("Mary-Jane Watson", "{fi}{last}@acme.com") == "mwatson@acme.com"


def test_construct_email_rejects_empty_and_bad_pattern():
    assert construct_email("", "{first}@acme.com") is None
    assert construct_email("日本 人", "{first}@acme.com") is None
    assert construct_email("Ann Lee", "{nope}@acme.com") is None


def test_choose_pattern_needs_two_votes():
    assert choose_pattern(["priya.sharma@acme.com"], "acme.com") is None
    assert (
        choose_pattern(["priya.sharma@acme.com", "arun.mehta@acme.com"], "acme.com")
        == FALLBACK
    )
    two = ["psharma@acme.com", "amehta@acme.com", "jane.doe@acme.com"]
    assert choose_pattern(two, "acme.com") == "{fi}{last}@acme.com"


def _commit_client(emails):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/commits" in str(request.url):
            items = [{"commit": {"author": {"email": e}, "committer": {"email": ""}}} for e in emails]
            return httpx.Response(200, json={"items": items})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_detection_single_sample_falls_back():
    with _commit_client(["psharma@acme.com"]) as client:
        assert email_pattern_detection("acme.com", client) == FALLBACK


def test_detection_two_samples_trusted():
    with _commit_client(["psharma@acme.com", "amehta@acme.com"]) as client:
        assert email_pattern_detection("acme.com", client) == "{fi}{last}@acme.com"


def test_detection_http_error_falls_back():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x", request=request)

    with httpx.Client(transport=httpx.MockTransport(boom)) as client:
        assert email_pattern_detection("acme.com", client) == FALLBACK


def test_detection_bad_json_falls_back():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="not json"))
    with httpx.Client(transport=transport) as client:
        assert email_pattern_detection("acme.com", client) == FALLBACK
