from jobdigest.openrouter import OpenRouterError
from jobdigest.summarize import summarize_digest

GUEST = [
    {
        "id": 1,
        "company": "Acme",
        "title": "Backend Intern",
        "location": "Pune",
        "url": "https://a.example/1",
        "score": 91,
        "why": "intern python",
    },
    {
        "id": 2,
        "company": "Globex",
        "title": "ML Intern",
        "location": "Bengaluru",
        "url": "https://g.example/2",
        "score": 88,
        "why": "rag intern",
    },
    {
        "id": 3,
        "company": "Initech",
        "title": "Platform Intern",
        "location": "Hyderabad",
        "url": "https://i.example/3",
        "score": 80,
        "why": "new-grad",
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
        GUEST, chat_fn=chat_fn, model="anthropic/claude-sonnet-4"
    )
    assert path == "model"
    assert "Acme" in text
    assert "Globex" in text
    assert "Initech" in text
    assert "FooCorp" not in text


def test_summarize_appends_missing_urls():
    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        return (
            "[id=1]\nAcme intern\n"
            "[id=2]\nGlobex intern\n"
            "[id=3]\nInitech intern\n"
        )

    text, path = summarize_digest(
        GUEST, chat_fn=chat_fn, model="anthropic/claude-sonnet-4"
    )
    assert path == "model"
    assert "https://a.example/1" in text
    assert "https://g.example/2" in text
    assert "https://i.example/3" in text


def test_summarize_fallback_on_error():
    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        raise OpenRouterError("down")

    text, path = summarize_digest(
        GUEST, chat_fn=chat_fn, model="anthropic/claude-sonnet-4"
    )
    assert path == "fallback"
    assert "https://a.example/1" in text
    assert "https://g.example/2" in text
    assert "https://i.example/3" in text
    assert "Acme" in text
    assert "Globex" in text
    assert "Initech" in text


def test_summarize_empty_guest():
    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        raise AssertionError("chat_fn should not be called")

    text, path = summarize_digest(
        [], chat_fn=chat_fn, model="anthropic/claude-sonnet-4"
    )
    assert text == ""
    assert path == "empty"
