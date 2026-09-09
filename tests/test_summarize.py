from jobdigest.openrouter import OpenRouterError
from jobdigest.summarize import fallback_bullets, render_card, summarize_digest

GUEST = [
    {
        "id": 1,
        "company": "Acme",
        "title": "Backend Intern",
        "location": "Pune",
        "url": "https://a.example/1",
        "score": 91,
        "why": "intern python",
        "age_chip": "4h",
        "source_chip": "ats",
        "ats": "greenhouse",
    },
    {
        "id": 2,
        "company": "Globex",
        "title": "ML Intern",
        "location": "Bengaluru",
        "url": "https://g.example/2",
        "score": 88,
        "why": "rag intern",
        "age_chip": "4h",
        "source_chip": "ats",
        "ats": "greenhouse",
    },
    {
        "id": 3,
        "company": "Initech",
        "title": "Platform Intern",
        "location": "Hyderabad",
        "url": "https://i.example/3",
        "score": 80,
        "why": "new-grad",
        "age_chip": "4h",
        "source_chip": "ats",
        "ats": "greenhouse",
    },
]


def test_summarize_drops_invented_ids():
    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        assert json_mode is False
        return (
            "Today\n"
            "[id=1]\nAcme intern\nhttps://a.example/1\n"
            "[id=2]\nGlobex intern\nhttps://g.example/2\n"
            "[id=3]\nInitech intern\nhttps://i.example/3\n"
            "[id=99]\nFooCorp\nhttps://evil.example/99\n"
        )

    text, path = summarize_digest(
        GUEST,
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        prompt_version="v1",
    )
    assert path == "model"
    assert "Acme" in text
    assert "Globex" in text
    assert "Initech" in text
    assert "FooCorp" not in text
    assert "intern python" in text
    assert "Why you:" in text


def test_summarize_appends_missing_urls():
    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        return (
            "[id=1]\nAcme intern\n"
            "[id=2]\nGlobex intern\n"
            "[id=3]\nInitech intern\n"
        )

    text, path = summarize_digest(
        GUEST,
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        prompt_version="v1",
    )
    assert path == "model"
    assert "https://a.example/1" in text
    assert "https://g.example/2" in text
    assert "https://i.example/3" in text


def test_summarize_fallback_on_error():
    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        raise OpenRouterError("down")

    text, path = summarize_digest(
        GUEST,
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        prompt_version="v1",
    )
    assert path == "fallback"
    assert "https://a.example/1" in text
    assert "https://g.example/2" in text
    assert "https://i.example/3" in text
    assert "Acme" in text
    assert "Globex" in text
    assert "Initech" in text
    assert "intern python" in text
    assert "91" in text
    assert "Why you:" in text


def test_summarize_empty_guest():
    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        raise AssertionError("chat_fn should not be called")

    text, path = summarize_digest(
        [],
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        prompt_version="v1",
    )
    assert text == ""
    assert path == "empty"


def test_fallback_bullets_includes_why_score_chips():
    text = fallback_bullets(GUEST)
    assert "intern python" in text
    assert "91" in text
    assert "4h" in text
    assert "ats" in text
    assert "Why you:" in text
    assert "https://a.example/1" in text


def test_render_card_is_two_lines():
    text = render_card(GUEST[0], 1)
    assert "\n\n" not in text
    assert len(text.split("\n")) == 2


def test_render_card_still_two_lines_with_suspect_chip():
    row = dict(GUEST[0], legitimacy_chip="suspect:90d")
    text = render_card(row, 1)
    assert "\n\n" not in text
    assert len(text.split("\n")) == 2
    assert "suspect:90d" in text.split("\n")[0]


def test_render_card_chip_on_line1_not_in_why_you():
    row = dict(GUEST[0], legitimacy_chip="suspect:mismatch")
    text = render_card(row, 1)
    line1, line2 = text.split("\n")
    assert "suspect:mismatch" in line1
    assert "suspect" not in line2
    assert line2.startswith("Why you:")
    assert "intern python" in line2


def test_render_card_omits_empty_location_slot():
    row = dict(GUEST[0], location="")
    text = render_card(row, 1)
    assert " | | " not in text
    assert "Pune" not in text.split("\n")[0]


def test_render_card_collapses_why_newlines():
    row = dict(GUEST[0], why="a\n\nb")
    text = render_card(row, 1)
    line2 = text.split("\n")[1]
    assert "a b" in line2
    assert "\n\n" not in text


def test_render_card_gap_location():
    row = dict(GUEST[0], location_ok=False)
    text = render_card(row, 1)
    assert "Gap: location" in text


def test_summarize_model_path_uses_renderer_not_model_card_body():
    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        return "Today\n[id=1]\nIGNORE THIS TITLE\n"

    text, path = summarize_digest(
        GUEST,
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        prompt_version="v1",
    )
    assert path == "model"
    assert "IGNORE THIS TITLE" not in text
    assert "Backend Intern" in text
    assert "intern python" in text


def test_summarize_system_includes_prompt_version():
    seen = {}

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        seen["system"] = messages[0]["content"]
        return "Today intern roles"

    text, path = summarize_digest(
        GUEST,
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        prompt_version="v1",
    )
    assert path == "model"
    assert seen["system"].startswith("prompt_version: v1\n")
    assert text
