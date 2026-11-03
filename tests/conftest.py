from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_live_telegram(monkeypatch):
    """No test may reach the Telegram API: clear credentials and block the real POST.

    Tests that exercise sending inject `http_post` (or set the env vars and
    monkeypatch `jobdigest.telegram._httpx_post` themselves).
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("JOBDIGEST_REQUIRE_TELEGRAM", raising=False)

    def _blocked(url, json=None, timeout=30.0):
        raise AssertionError(f"test attempted a live Telegram POST to {url!r}; inject http_post")

    monkeypatch.setattr("jobdigest.telegram._httpx_post", _blocked)
