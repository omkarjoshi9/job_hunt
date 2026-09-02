from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"


def test_ci_stays_pytest_only():
    text = (WF / "ci.yml").read_text(encoding="utf-8")
    assert "pytest" in text
    assert "cron" not in text
    assert "telegram" not in text.lower()
    assert "DATABASE_URL" not in text
    assert "PROFILE_YAML" not in text
    assert "MODELS_YAML" not in text
    assert "PORTALS_YAML" not in text
    assert "FEEDS_YAML" not in text
    assert "GOOGLE_SHEET" not in text
    assert "sheet-sync" not in text
    assert "playwright install" not in text
    assert "chromium" not in text.lower()


def test_collect_workflow():
    text = (WF / "collect.yml").read_text(encoding="utf-8")
    assert "0 */6 * * *" in text
    assert "workflow_dispatch" in text
    assert "jobdigest-collect" in text
    assert "cancel-in-progress: false" in text
    assert "--portal-fetch http" in text
    assert "--ats-only" not in text
    assert "playwright install" not in text
    assert "python -m jobdigest collect" in text
    assert "python-version: \"3.12\"" in text or "python-version: '3.12'" in text
    assert "secrets.DATABASE_URL" in text
    assert "secrets.TELEGRAM_BOT_TOKEN" in text
    assert "secrets.TELEGRAM_CHAT_ID" in text
    assert "JOBDIGEST_ENABLE" not in text
    assert "secrets.PROFILE_YAML" in text
    assert "secrets.COMPANIES_YAML" in text
    assert "secrets.MODELS_YAML" in text
    assert "secrets.PORTALS_YAML" in text
    assert "secrets.FEEDS_YAML" in text
    assert "JOBDIGEST_REQUIRE_TELEGRAM" in text
    assert "git commit" not in text
    assert "jobs.db" not in text
    assert "sk-or-" not in text
    assert "sheet-sync" not in text


def test_collect_browser_workflow():
    text = (WF / "collect-browser.yml").read_text(encoding="utf-8")
    assert "0 3 * * *" in text
    assert "workflow_dispatch" in text
    assert "jobdigest-collect-browser" in text
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 40" in text
    assert "~/.cache/ms-playwright" in text
    assert "playwright install-deps chromium" in text
    assert "playwright install chromium" in text
    assert "--skip-ats" in text
    assert "--skip-feeds" in text
    assert "--portal-fetch dynamic" in text
    assert "--portal-fetch stealth" in text
    assert "python -m jobdigest collect" in text
    assert "python -m jobdigest digest" not in text
    assert "sheet-sync" not in text
    assert "sk-or-" not in text
    assert "secrets.DATABASE_URL" in text
    assert "secrets.TELEGRAM_BOT_TOKEN" in text
    assert "JOBDIGEST_REQUIRE_TELEGRAM" in text
    assert "secrets.PORTALS_YAML" in text
    assert "python-version: \"3.12\"" in text or "python-version: '3.12'" in text
    assert "git commit" not in text
    assert "jobs.db" not in text


def test_digest_workflow_tz():
    text = (WF / "digest.yml").read_text(encoding="utf-8")
    assert "*/15 * * * *" in text
    assert "secrets.JOBDIGEST_CRON" in text
    assert "python3 -m jobdigest.cron" in text
    assert "needs.gate.outputs.due" in text
    assert "30 4 * * *" not in text
    assert "0 4 * * *" not in text
    assert "workflow_dispatch" in text
    assert "jobdigest-digest" in text
    assert "cancel-in-progress: false" in text
    assert "python -m jobdigest digest" in text
    assert "secrets.OPENROUTER_API_KEY" in text
    assert "secrets.DATABASE_URL" in text
    assert "secrets.PROFILE_YAML" in text
    assert "secrets.COMPANIES_YAML" in text
    assert "secrets.MODELS_YAML" in text
    assert "secrets.PORTALS_YAML" in text
    assert "secrets.FEEDS_YAML" in text
    assert "JOBDIGEST_REQUIRE_TELEGRAM" in text
    assert "python -m jobdigest sheet-sync" in text
    assert "secrets.GOOGLE_SHEET_ID" in text
    assert "secrets.GOOGLE_SERVICE_ACCOUNT_JSON" in text
    assert "JOBDIGEST_REQUIRE_SHEET" in text
    assert "git commit" not in text
    assert "--ats-only" not in text
    assert "playwright install" not in text


def test_start_sh_uses_http_portals():
    text = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert "--portal-fetch http" in text
    assert "--ats-only" not in text
    assert "digest" in text
    assert "sheet-sync" in text
