from jobdigest.ats.base import parse_posted_at, posted_at_from_item


def test_parse_posted_at_epoch_seconds():
    assert parse_posted_at(1_700_000_000) == "2023-11-14T22:13:20Z"


def test_parse_posted_at_epoch_millis():
    assert parse_posted_at(1_700_000_000_000) == "2023-11-14T22:13:20Z"


def test_parse_posted_at_iso_z():
    assert parse_posted_at("2024-06-01T12:30:00Z") == "2024-06-01T12:30:00Z"


def test_parse_posted_at_iso_with_millis_strips_fraction():
    assert parse_posted_at("2024-06-01T12:30:00.123Z") == "2024-06-01T12:30:00Z"


def test_parse_posted_at_date_only():
    assert parse_posted_at("2024-06-01") == "2024-06-01T00:00:00Z"


def test_parse_posted_at_rejects_bool_and_empty():
    assert parse_posted_at(True) is None
    assert parse_posted_at(False) is None
    assert parse_posted_at("") is None
    assert parse_posted_at(None) is None
    assert parse_posted_at("not-a-date") is None


def test_posted_at_from_item_first_nonempty_key():
    item = {"publishedDate": "2024-01-02T00:00:00Z", "publishedAt": ""}
    assert posted_at_from_item(item, "publishedAt", "publishedDate") == "2024-01-02T00:00:00Z"
    assert posted_at_from_item({"x": None}, "x", "y") is None
