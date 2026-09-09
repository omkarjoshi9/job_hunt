from pathlib import Path

import json

from jobdigest.telegram import (
    action_markup,
    deliver,
    get_updates,
    pager_markup,
    send_action_card,
    send_guest_pager,
    send_message,
    split_message,
)

ROOT = Path(__file__).resolve().parents[1]


class _OkPost:
    status_code = 200
    text = '{"ok": true}'

    def json(self):
        return json.loads(self.text)


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


def test_split_message_keeps_two_line_cards_on_double_newline():
    cards = []
    for i in range(12):
        line1 = (
            f"{i + 1}. Co{i:02d} — Software Engineer Intern | Pune | 90 | 4h | ats | "
            f"https://example.com/jobs/{i}-" + ("x" * 80)
        )
        line2 = "Why you: " + ("W" * 160)
        cards.append(f"{line1}\n{line2}")
    text = "\n\n".join(cards)
    parts = split_message(text, limit=3800)
    assert len(parts) >= 2
    assert all(len(p) <= 3800 for p in parts)
    for part in parts:
        stripped = part.lstrip()
        assert not stripped.startswith("Why you:")
        assert not stripped.startswith("Gap:")


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


def test_callback_data_fits_64_bytes():
    big = 10**18
    pager = pager_markup([{"id": big}])
    data = pager["inline_keyboard"][0][0]["callback_data"]
    assert data == f"o:{big}"
    assert len(data.encode("ascii")) <= 64
    markup = action_markup(big, "https://example.com/apply")
    for row in markup["inline_keyboard"]:
        for btn in row:
            if "callback_data" in btn:
                assert len(btn["callback_data"].encode("ascii")) <= 64
            if "url" in btn:
                assert "callback_data" not in btn


def test_send_guest_pager_posts_inline_o_callbacks(monkeypatch):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99")
    assert send_guest_pager([{"id": 42}, {"id": 43}], http_post=http_post) is True
    assert posts[0]["url"] == "https://api.telegram.org/bottok/sendMessage"
    body = posts[0]["json"]
    assert body["text"] == "Tap a number to open a card."
    assert "parse_mode" not in body
    kb = body["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["callback_data"] == "o:42"
    assert kb[0][1]["callback_data"] == "o:43"
    assert kb[0][0]["text"] == "1"


def test_send_action_card_apply_is_url_not_callback(monkeypatch):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99")
    assert send_action_card(
        "1. Acme — Intern\nWhy you: fit",
        job_id=7,
        apply_url="https://example.com/jobs/7",
        http_post=http_post,
    )
    body = posts[0]["json"]
    assert "parse_mode" not in body
    kb = body["reply_markup"]["inline_keyboard"]
    apply_btn = kb[0][0]
    assert apply_btn["url"] == "https://example.com/jobs/7"
    assert "callback_data" not in apply_btn
    callbacks = [btn["callback_data"] for btn in kb[1]]
    assert callbacks == ["s:7", "a:7", "h:7"]


def test_get_updates_allowed_updates_message_and_callback(monkeypatch):
    posts = []

    def http_post(url, json=None, timeout=None):
        _ = timeout
        posts.append({"url": url, "json": dict(json or {})})
        return _OkPost()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    get_updates(token="tok", offset=4, http_post=http_post)
    assert posts[0]["url"] == "https://api.telegram.org/bottok/getUpdates"
    assert posts[0]["json"]["offset"] == 4
    assert posts[0]["json"]["allowed_updates"] == ["message", "callback_query"]


def test_callback_data_w_fits_64_bytes():
    big = 10**18
    data = f"w:{big}"
    assert len(data.encode("ascii")) <= 64
    markup = action_markup(big, "https://example.com/apply")
    found = [
        btn["callback_data"]
        for row in markup["inline_keyboard"]
        for btn in row
        if btn.get("callback_data") == data
    ]
    assert found == [data]
    for row in markup["inline_keyboard"]:
        for btn in row:
            if "callback_data" in btn:
                assert len(btn["callback_data"].encode("ascii")) <= 64


def test_callback_data_m_fits_64_bytes():
    big = 10**18
    data = f"m:{big}"
    assert len(data.encode("ascii")) <= 64
    markup = action_markup(big, "https://example.com/apply")
    found = [
        btn["callback_data"]
        for row in markup["inline_keyboard"]
        for btn in row
        if btn.get("callback_data") == data
    ]
    assert found == [data]


def test_action_markup_why_me_own_row():
    markup = action_markup(7, "https://example.com/apply")
    kb = markup["inline_keyboard"]
    assert kb[0][0]["url"] == "https://example.com/apply"
    assert "callback_data" not in kb[0][0]
    assert [btn["text"] for btn in kb[1]] == ["Save", "Applied ✓", "Hide ✗"]
    assert kb[2] == [
        {"text": "Why me?", "callback_data": "w:7"},
        {"text": "Mail", "callback_data": "m:7"},
    ]
    assert len(kb[2]) == 2


def test_action_markup_hide_drops_why_me():
    markup = action_markup(
        7, "https://example.com/apply", status_buttons=False, why_me=False, mail=False
    )
    kb = markup["inline_keyboard"]
    assert all("callback_data" not in btn for row in kb for btn in row)
    assert not any(
        btn.get("text") in {"Why me?", "Mail"} for row in kb for btn in row
    )


def test_action_markup_applied_keeps_why_me():
    markup = action_markup(
        7, "https://example.com/apply", status_buttons=False, why_me=True
    )
    kb = markup["inline_keyboard"]
    texts = [btn["text"] for row in kb for btn in row]
    assert "Why me?" in texts
    assert "Mail" in texts
    assert "Save" not in texts
    assert "Applied ✓" not in texts
    assert "Hide ✗" not in texts
    why = [btn for row in kb for btn in row if btn.get("text") == "Why me?"]
    assert why == [{"text": "Why me?", "callback_data": "w:7"}]
    mail = [btn for row in kb for btn in row if btn.get("text") == "Mail"]
    assert mail == [{"text": "Mail", "callback_data": "m:7"}]
