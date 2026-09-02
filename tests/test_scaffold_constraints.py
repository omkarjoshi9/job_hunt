from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("openai",)


def test_pyproject_has_no_forbidden_deps():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    for word in FORBIDDEN:
        assert word not in text, word
    assert "scrapling[fetchers]" in text
    assert "playwright==1.62.0" in text
    assert "patchright==1.62.1" in text


def test_pyproject_has_httpx_scrapling_feedparser_gspread():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "httpx" in text
    assert "scrapling" in text
    assert "feedparser" in text
    assert "gspread" in text
    assert "psycopg" in text


def test_no_requirements_txt():
    assert not (ROOT / "requirements.txt").exists()


def test_env_example_has_i7_keys():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DATABASE_URL=" in text
    assert "JOBDIGEST_ENABLE=" not in text
    assert "JOBDIGEST_CRON=" in text
    assert "TELEGRAM_BOT_TOKEN=" in text
    assert "TELEGRAM_CHAT_ID=" in text


def test_readme_drops_hermes_documents_hosted():
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "hermes" not in text
    assert "openwa" not in text
    assert "30 4 * * *" in text
    assert "botfather" in text
    assert "neon" in text
    assert "--ats-only" in text
    assert "collect-browser" in text
    assert "--portal-fetch" in text
    assert "playwright install chromium" in text
    assert "0 3 * * *" in text
    assert "feedparser" in text
    assert "wheels" in text
    assert "http-only fetching" not in text
    assert "no playwright, chromium" not in text
