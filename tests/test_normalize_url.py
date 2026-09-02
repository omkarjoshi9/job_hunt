from jobdigest.normalize import job_identity, normalize_url


def test_host_case_and_trailing_slash():
    assert (
        normalize_url("https://JOBS.Example.com/role/")
        == "https://jobs.example.com/role"
    )


def test_utm_gclid_www_keep_other():
    got = normalize_url("https://www.example.com/a?utm_source=x&gclid=1&keep=1")
    assert got == "https://example.com/a?keep=1"


def test_fragment_dropped():
    assert normalize_url("https://example.com/a#section") == "https://example.com/a"


def test_job_identity_http_uses_normalize():
    col, norm = job_identity(
        "https://WWW.Example.com/x/?utm_source=y",
        "",
        "greenhouse",
        "1",
    )
    assert col == "https://WWW.Example.com/x/?utm_source=y"
    assert norm == "https://example.com/x"


def test_job_identity_synthetic_requires_external_id():
    assert job_identity("", "", "lever", "abc") == (None, "ats://lever/abc")
    assert job_identity("", "", "lever", "") == (None, None)
