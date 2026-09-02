from jobdigest.ats.base import FetchStatus, classify_http_status, decode_html_content, empty_or_ok, join_locations


def test_classify_404_410_redirects_are_dead_token():
    for code in (404, 410, 301, 302, 303, 307, 308):
        assert classify_http_status(code) is FetchStatus.DEAD_TOKEN


def test_classify_200_means_parse_body():
    assert classify_http_status(200) is None


def test_classify_403_429_5xx_are_error():
    for code in (403, 429, 500, 502, 503):
        assert classify_http_status(code) is FetchStatus.ERROR


def test_empty_or_ok():
    assert empty_or_ok(0) is FetchStatus.EMPTY_BOARD
    assert empty_or_ok(1) is FetchStatus.OK


def test_decode_html_double_unescape():
    text = decode_html_content("&amp;lt;p&amp;gt;Bengaluru intern&amp;lt;/p&amp;gt;")
    assert text == "Bengaluru intern"
    assert "&lt;" not in text
    assert "<p>" not in text


def test_join_locations_dedupes_and_skips_blank():
    assert join_locations(["Bengaluru", None, "Bengaluru", " India "]) == "Bengaluru | India"
