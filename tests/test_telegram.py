from pathlib import Path

from jobdigest.telegram import deliver, send_message, split_message

ROOT = Path(__file__).resolve().parents[1]


class _OkPost:
    status_code = 200
    text = '{"ok": true}'


class _BadPost:
    status_code = 400
    text = "bad"


def test_split_message_keeps_short_text():
    assert split_message("hello") == ["hello"]


def test_split_message_breaks_long_block():
    text = "a" * 4000
    parts = split_message(text, limit=3800)
    assert all(len(p) <= 3800 for p in parts)
    assert "".join(parts) == text


def test_send_message_posts_plain_text_no_parse_mode():
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    send_message("hi", token="tok", chat_id="99", http_post=http_post)
    assert posts[0]["url"] == "https://api.telegram.org/bottok/sendMessage"
    assert posts[0]["json"] == {"chat_id": "99", "text": "hi"}
    assert "parse_mode" not in posts[0]["json"]


def test_deliver_skips_empty():
    posts = []

    def http_post(url, json=None, timeout=None):
        posts.append(1)
        return _OkPost()

    assert deliver("", required=True, http_post=http_post) is True
    assert posts == []


def test_deliver_required_missing_creds_fails_when_flag_set(monkeypatch, capsys):
    monkeypatch.setenv("JOBDIGEST_REQUIRE_TELEGRAM", "1")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert deliver("COLLECT FAILED", required=True) is False
    assert "TELEGRAM FAILED" in capsys.readouterr().err


def test_deliver_required_missing_creds_ok_locally(monkeypatch, capsys):
    monkeypatch.delenv("JOBDIGEST_REQUIRE_TELEGRAM", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert deliver("hello", required=True) is True
    assert capsys.readouterr().err == ""


def test_deliver_http_error_returns_false(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    def http_post(url, json=None, timeout=None):
        _ = url, json, timeout
        return _BadPost()

    assert deliver("hello", required=True, http_post=http_post) is False


def test_live_telegram_url_only_in_telegram_module():
    src = ROOT / "src" / "jobdigest"
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name == "telegram.py":
            assert "api.telegram.org" in text
            continue
        assert "api.telegram.org" not in text, path
