from jobdigest.portals.session import (
    FETCH_DYNAMIC,
    FETCH_HTTP,
    FETCH_STEALTH,
    UA,
    SessionBundle,
    close_bundle,
    get_html,
    open_bundle,
    portal_fetch_mode,
)


class _Page:
    def __init__(self, html: str, status: int = 200):
        self.status = status
        self.html_content = html


class _HttpSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return _Page("<p>http</p>")

    def fetch(self, url, **kwargs):
        raise AssertionError("http mode must use get")


class _BrowserSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        raise AssertionError("browser mode must use fetch")

    def fetch(self, url, **kwargs):
        self.calls.append(("fetch", url, kwargs))
        return _Page("<p>browser</p>")


def test_portal_fetch_mode_defaults_http():
    assert portal_fetch_mode({}) == FETCH_HTTP
    assert portal_fetch_mode({"fetch": ""}) == FETCH_HTTP
    assert portal_fetch_mode({"fetch": "Dynamic"}) == FETCH_DYNAMIC


def test_get_html_http_uses_get_not_fetch():
    session = _HttpSession()
    sleeps = []
    status, html, page = get_html(
        session,
        "https://example.com/a",
        sleep=sleeps.append,
        last_host={},
        fetch=FETCH_HTTP,
    )
    assert status == 200
    assert html == "<p>http</p>"
    assert page.status == 200
    assert session.calls[0][0] == "get"
    assert session.calls[0][2]["stealthy_headers"] is False
    assert sleeps == []


def test_get_html_same_host_sleeps():
    session = _HttpSession()
    sleeps = []
    last_host = {"host": "example.com"}
    get_html(
        session,
        "https://example.com/b",
        sleep=sleeps.append,
        last_host=last_host,
        fetch=FETCH_HTTP,
    )
    assert sleeps == [3.5]


def test_get_html_dynamic_uses_fetch():
    session = _BrowserSession()
    status, html, _page = get_html(
        session,
        "https://spa.example/jobs",
        sleep=lambda _: None,
        last_host={},
        fetch=FETCH_DYNAMIC,
    )
    assert status == 200
    assert html == "<p>browser</p>"
    assert session.calls[0][0] == "fetch"
    assert session.calls[0][2]["google_search"] is False
    assert "User-Agent" in session.calls[0][2]["extra_headers"]
    assert "solve_cloudflare" not in session.calls[0][2]


def test_get_html_stealth_passes_solve_cloudflare():
    session = _BrowserSession()
    get_html(
        session,
        "https://cf.example/jobs",
        sleep=lambda _: None,
        last_host={},
        fetch=FETCH_STEALTH,
        solve_cloudflare=True,
    )
    assert session.calls[0][2]["solve_cloudflare"] is True


def test_get_html_http_merges_extra_headers():
    session = _HttpSession()
    get_html(
        session,
        "https://intern.hasjob.co/",
        sleep=lambda _: None,
        last_host={},
        fetch=FETCH_HTTP,
        extra_headers={"Accept": "application/json"},
    )
    headers = session.calls[0][2]["headers"]
    assert headers["User-Agent"] == UA
    assert headers["Accept"] == "application/json"


def test_get_html_dynamic_merges_extra_headers():
    session = _BrowserSession()
    get_html(
        session,
        "https://intern.hasjob.co/",
        sleep=lambda _: None,
        last_host={},
        fetch=FETCH_DYNAMIC,
        extra_headers={"Accept": "application/json"},
    )
    extra = session.calls[0][2]["extra_headers"]
    assert extra["User-Agent"] == UA
    assert extra["Accept"] == "application/json"


def test_open_bundle_empty_does_not_open_sessions():
    bundle = open_bundle([])
    assert bundle.http is None
    assert bundle.dynamic is None
    assert bundle.stealth is None
    assert bundle.errors == {}
    close_bundle(bundle)
    close_bundle(None)


def test_open_bundle_records_mode_errors(monkeypatch):
    def boom():
        raise RuntimeError("no chrome")

    monkeypatch.setattr("jobdigest.portals.session.open_dynamic_session", boom)
    monkeypatch.setattr("jobdigest.portals.session.open_stealth_session", boom)
    bundle = open_bundle([FETCH_DYNAMIC, FETCH_STEALTH])
    assert bundle.dynamic is None
    assert "no chrome" in bundle.errors[FETCH_DYNAMIC]
    assert "no chrome" in bundle.errors[FETCH_STEALTH]


def test_session_bundle_get():
    bundle = SessionBundle(http="h", dynamic="d", stealth="s")
    assert bundle.get(FETCH_HTTP) == "h"
    assert bundle.get(FETCH_DYNAMIC) == "d"
    assert bundle.get(FETCH_STEALTH) == "s"
    assert bundle.get("other") is None
