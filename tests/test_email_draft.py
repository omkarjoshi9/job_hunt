from dataclasses import replace

from jobdigest.config import load_profile
from jobdigest.email_draft import draft_email, format_telegram


def test_draft_uses_recruiter_template_and_omits_empty_github():
    profile = load_profile()
    profile = replace(profile, github_url="")
    draft = draft_email(
        profile=profile,
        job_row={"title": "Software Engineer Intern", "company": "Acme"},
        contact={"name": "Arun Mehta", "email": "arun@acme.com", "role": "Recruiter", "source": "jd", "confidence": 90},
    )
    assert "application" in draft.subject.lower()
    assert draft.to == "arun@acme.com"
    assert "GitHub" not in draft.body
    assert "Send manually" in format_telegram(draft)
    assert draft.mailto.startswith("mailto:arun@acme.com")


def test_draft_cold_includes_github_and_fallback_observation(monkeypatch):
    profile = replace(load_profile(), github_url="https://github.com/example")
    called = {"n": 0}

    def boom(*args, **kwargs):
        called["n"] += 1
        raise RuntimeError("model down")

    draft = draft_email(
        profile=profile,
        job_row={"title": "Software Engineer Intern", "company": "Acme"},
        contact={
            "name": "Priya Sharma",
            "email": "priya.sharma@acme.com",
            "role": "Hiring Manager",
            "source": "jd",
            "confidence": 100,
        },
        chat_fn=boom,
        model="anthropic/claude-sonnet-4",
        api_key="k",
    )
    assert called["n"] == 1
    assert "https://github.com/example" in draft.body
    from jobdigest.email_draft import _fallback_observations

    assert any(obs in draft.body for obs in _fallback_observations())
    text = format_telegram(draft)
    assert "Subject:" in text
    assert "To: priya.sharma@acme.com" in text
    assert "source=jd" in text
    assert "Send manually — no SMTP" in text


def test_draft_without_email_skips_mailto():
    profile = load_profile()
    draft = draft_email(
        profile=profile,
        job_row={"title": "Intern", "company": "Acme"},
        contact={"name": "Priya Sharma", "role": "Hiring Manager", "source": "jd"},
    )
    assert draft.to == ""
    assert draft.mailto == ""
    assert "(no email)" in format_telegram(draft)


def test_fallback_selection_deterministic_and_uses_all():
    from jobdigest.email_draft import _fallback_observations, _select_fallback

    items = _fallback_observations()
    assert len(items) > 1
    assert _select_fallback("a|b|c") == _select_fallback("a|b|c")
    picked = {_select_fallback(f"co{i}|t|e") for i in range(200)}
    assert picked == set(items)
