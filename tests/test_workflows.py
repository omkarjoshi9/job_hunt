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


def test_collect_workflow():
    text = (WF / "collect.yml").read_text(encoding="utf-8")
    assert "0 */6 * * *" in text
    assert "workflow_dispatch" in text
    assert "jobdigest-collect" in text
    assert "cancel-in-progress: false" in text
    assert "--ats-only" in text
    assert "python -m jobdigest collect" in text
    assert "python-version: \"3.12\"" in text or "python-version: '3.12'" in text
    assert "secrets.DATABASE_URL" in text
    assert "secrets.TELEGRAM_BOT_TOKEN" in text
    assert "secrets.TELEGRAM_CHAT_ID" in text
    assert "secrets.JOBDIGEST_ENABLE" in text
    assert "secrets.PROFILE_YAML" in text
    assert "secrets.COMPANIES_YAML" in text
    assert "secrets.MODELS_YAML" in text
    assert "secrets.PORTALS_YAML" in text
    assert "secrets.FEEDS_YAML" in text
    assert "vars.JOBDIGEST_ENABLE" not in text
    assert "JOBDIGEST_REQUIRE_TELEGRAM" in text
    assert "git commit" not in text
    assert "jobs.db" not in text
    assert "sk-or-" not in text
    assert "sheet-sync" not in text


def test_digest_workflow_tz():
    text = (WF / "digest.yml").read_text(encoding="utf-8")
    assert "30 4 * * *" in text
    assert "0 4 * * *" not in text.replace("30 4 * * *", "")
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
