from dataclasses import replace

from jobdigest.blurb import draft_blurb, ground
from jobdigest.config import load_profile


JOB = {
    "id": 1,
    "title": "Software Engineer Intern",
    "company": "Acme",
    "location_raw": "Pune",
    "description_text": "Intern role in Pune building Python services",
    "employment_hint": "intern",
    "workplace_hint": "hybrid",
}


def _words(n: int, word: str = "experience") -> str:
    return " ".join([word] * n)


def test_prompt_omits_name_and_cgpa():
    # Distinctive values: the placeholder name "Example" is a substring of the
    # placeholder internship "Example intern", which legitimately appears in the prompt.
    profile = replace(load_profile(), name="Zyx Qwertyson", cgpa=9.37)
    captured: list[list[dict]] = []

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = model, api_key, json_mode, client
        captured.append(messages)
        cite = profile.internships[0]
        return f"CITE: {cite}\n\n{_words(90)}"

    draft_blurb(
        profile=profile,
        job_row=JOB,
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        api_key="",
        prompt_version="v1",
    )
    blob = "\n".join(m["content"] for m in captured[0])
    assert profile.name not in blob
    assert str(profile.cgpa) not in blob
    assert "raw_json" not in blob
    assert "prompt_version: v1" in captured[0][0]["content"]
    assert "<<<JOB>>>" in blob
    assert "untrusted" in captured[0][0]["content"]


def test_cite_must_match_internship():
    profile = load_profile()
    calls = {"n": 0}

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, json_mode, client
        calls["n"] += 1
        return f"CITE: Not a real internship\n\n{_words(90)}"

    text, path = draft_blurb(
        profile=profile,
        job_row=JOB,
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        api_key="",
        prompt_version="v1",
    )
    assert path == "refuse"
    assert text == "Can't draft: model cited an internship that is not in the profile."
    assert "Not a real internship" not in text
    assert _words(90) not in text
    assert calls["n"] == 1


def test_ungrounded_camelcase_flagged():
    profile = replace(load_profile(), internships=["Example intern"])
    raw = "CITE: Example intern\n\nFooDB is great"
    body, flags = ground(raw, profile, JOB)
    assert "FooDB" in flags
    assert "Example intern" not in flags
    assert "FooDB" in body


def test_empty_internships_does_not_call_chat_fn():
    profile = replace(load_profile(), internships=[])
    calls = {"n": 0}

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, json_mode, client
        calls["n"] += 1
        raise AssertionError("chat_fn must not be called")

    text, path = draft_blurb(
        profile=profile,
        job_row=JOB,
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        api_key="",
        prompt_version="v1",
    )
    assert path == "refuse"
    assert text == "Can't draft: no internships in profile to cite."
    assert calls["n"] == 0


def test_json_mode_is_false():
    profile = load_profile()
    seen: list[bool] = []

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, client
        seen.append(json_mode)
        cite = profile.internships[0]
        return f"CITE: {cite}\n\n{_words(90)}"

    text, path = draft_blurb(
        profile=profile,
        job_row=JOB,
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        api_key="",
        prompt_version="v1",
    )
    assert path == "model"
    assert seen == [False]
    assert text.startswith("CITE:")


def test_word_count_flag_does_not_recall_model():
    profile = load_profile()
    calls = {"n": 0}

    def chat_fn(messages, *, model, api_key="", json_mode=False, client=None):
        _ = messages, model, api_key, json_mode, client
        calls["n"] += 1
        cite = profile.internships[0]
        return f"CITE: {cite}\n\nexperience built intern"

    text, path = draft_blurb(
        profile=profile,
        job_row=JOB,
        chat_fn=chat_fn,
        model="anthropic/claude-sonnet-4",
        api_key="",
        prompt_version="v1",
    )
    assert path == "model"
    assert calls["n"] == 1
    body, flags = ground(text, profile, JOB)
    assert calls["n"] == 1
    assert any(f.startswith("length:") for f in flags)
    assert "length: 3" in flags
    assert "experience built intern" in body


def test_openrouter_error_returns_refusal():
    from jobdigest.blurb import MODEL_ERROR
    from jobdigest.openrouter import OpenRouterError

    def chat_fn(messages, **kw):
        raise OpenRouterError("boom")

    text, path = draft_blurb(
        profile=load_profile(),
        job_row=JOB,
        chat_fn=chat_fn,
        model="m",
        api_key="",
        prompt_version="v1",
    )
    assert (text, path) == (MODEL_ERROR, "refuse")
