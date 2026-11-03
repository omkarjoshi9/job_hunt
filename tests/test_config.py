from pathlib import Path

import yaml

from jobdigest.config import load_marketplaces, load_models, load_profile

ROOT = Path(__file__).resolve().parents[1]


def test_profile_filter_types():
    p = load_profile()
    assert p.batch == 2027
    assert isinstance(p.batch, int)
    assert isinstance(p.cgpa, float)
    assert p.digest_max == 12
    assert isinstance(p.watchlist, list)
    assert isinstance(p.urgent_score_min, int)
    assert isinstance(p.urgent_daily_cap, int)
    assert isinstance(p.github_url, str)
    assert p.timezone == "Asia/Kolkata"
    assert p.work_auth == "India"
    assert "intern" in p.role_types
    assert "new-grad" in p.role_types
    assert "Bengaluru" in p.relocate
    assert p.reject_remote_us is True
    assert p.min_yoe_reject == 3
    assert r"\blead\b" in p.title_reject_regex
    assert r"\bfellow\b" in p.title_reject_regex
    assert r"\bvp\b" in p.title_reject_regex
    assert r"\bdirector\b" in p.title_reject_regex
    assert r"\bhead of\b" in p.title_reject_regex
    assert r"\bmanager\b" in p.title_reject_regex
    assert r"\bsenior\b" in p.title_reject_regex
    assert r"\bstaff\b" in p.title_reject_regex


def test_load_profile_without_github_url_key(tmp_path, monkeypatch):
    src = (ROOT / "config" / "profile.example.yaml").read_text(encoding="utf-8")
    src = "\n".join(
        line for line in src.splitlines() if not line.startswith("github_url")
    )
    (tmp_path / "profile.yaml").write_text(src + "\n", encoding="utf-8")
    monkeypatch.setenv("JOBDIGEST_CONFIG_DIR", str(tmp_path))
    p = load_profile()
    assert p.github_url == ""


def test_profile_has_no_digest_hour():
    p = load_profile()
    assert not hasattr(p, "digest_hour")
    example = (ROOT / "config" / "profile.example.yaml").read_text(encoding="utf-8")
    assert "digest_hour" not in example


def test_example_profile_has_no_personal_identity():
    text = (ROOT / "config" / "profile.example.yaml").read_text(encoding="utf-8")
    assert "Omkar" not in text
    assert "8.88" not in text
    assert "PICT" not in text
    data = yaml.safe_load(text)
    assert data.get("watchlist") == []
    assert data.get("urgent_score_min") == 85
    assert data.get("urgent_daily_cap") == 3
    assert "github_url" in data
    assert data.get("github_url") in ("", None)
    reject = str(data["title_reject_regex"])
    assert r"\blead\b" in reject
    assert r"\bfellow\b" in reject
    assert r"\bhead of\b" in reject
    assert r"\bdirector\b" in reject
    assert r"\bvp\b" in reject


def test_models_both_keys_not_free():
    m = load_models()
    assert m.score_model
    assert m.summarize_model
    assert not m.score_model.endswith(":free")
    assert not m.summarize_model.endswith(":free")
    assert m.batch_size == 12


def test_score_prompt_version_is_nonempty_not_free():
    m = load_models()
    assert isinstance(m.score_prompt_version, str)
    assert m.score_prompt_version.strip()
    assert not m.score_prompt_version.endswith(":free")
    assert isinstance(m.summarize_prompt_version, str)
    assert m.summarize_prompt_version.strip()
    assert not m.summarize_prompt_version.endswith(":free")


def test_example_models_score_prompt_version_present():
    data = yaml.safe_load(
        (ROOT / "config" / "models.example.yaml").read_text(encoding="utf-8")
    )
    pv = str(data["score"]["prompt_version"])
    assert pv.strip()
    assert not pv.endswith(":free")
    assert not str(data["score"]["model"]).endswith(":free")
    assert str(data["score"]["model"]) != "openrouter/auto"
    assert str(data["summarize"]["model"]) != "openrouter/auto"
    assert str(data["summarize"]["prompt_version"]).strip()


def test_example_blurb_model_is_paid_and_distinct_from_score():
    data = yaml.safe_load(
        (ROOT / "config" / "models.example.yaml").read_text(encoding="utf-8")
    )
    blurb = data["blurb"]
    score = data["score"]
    assert blurb is not score
    pv = str(blurb["prompt_version"])
    assert pv.strip()
    model = str(blurb["model"])
    assert not model.endswith(":free")
    assert model != "openrouter/auto"
    assert "prompt_version" in blurb
    assert "prompt_version" in score


def test_load_models_without_blurb_block(tmp_path, monkeypatch):
    (tmp_path / "models.yaml").write_text(
        "score:\n"
        "  model: openai/gpt-4o-mini\n"
        "  prompt_version: v5\n"
        "summarize:\n"
        "  model: anthropic/claude-sonnet-4\n"
        "  prompt_version: v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBDIGEST_CONFIG_DIR", str(tmp_path))
    m = load_models()
    assert m.blurb_model is None
    assert m.blurb_prompt_version is None
    assert m.score_model == "openai/gpt-4o-mini"
    assert m.score_prompt_version == "v5"


def test_load_marketplaces_from_config_dir(tmp_path, monkeypatch):
    (tmp_path / "marketplaces.yaml").write_text(
        "marketplaces:\n"
        "  - name: naukri-intern-india\n"
        "    site: naukri\n"
        "    enabled: false\n"
        "    search_term: intern\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBDIGEST_CONFIG_DIR", str(tmp_path))
    data = load_marketplaces()
    assert data["marketplaces"][0]["site"] == "naukri"
    assert data["marketplaces"][0]["enabled"] is False


def _write_models(tmp_path, body):
    (tmp_path / "models.yaml").write_text(body, encoding="utf-8")


def test_models_batch_size_under_score_and_legacy_top_level(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBDIGEST_CONFIG_DIR", str(tmp_path))
    base = "summarize:\n  model: m2\n  prompt_version: v1\n"
    _write_models(
        tmp_path, "score:\n  model: m1\n  prompt_version: v1\n  batch_size: 7\n" + base
    )
    assert load_models().batch_size == 7
    _write_models(
        tmp_path, "score:\n  model: m1\n  prompt_version: v1\n" + base + "batch_size: 9\n"
    )
    assert load_models().batch_size == 9
    _write_models(tmp_path, "score:\n  model: m1\n  prompt_version: v1\n" + base)
    assert load_models().batch_size == 12


def test_example_models_batch_size_only_under_score():
    data = yaml.safe_load(
        (ROOT / "config" / "models.example.yaml").read_text(encoding="utf-8")
    )
    assert "batch_size" not in data
    assert data["score"]["batch_size"] == 12


def test_example_fallback_logs_warning(tmp_path, monkeypatch, caplog):
    import logging

    src = (ROOT / "config" / "feeds.example.yaml").read_text(encoding="utf-8")
    (tmp_path / "feeds.example.yaml").write_text(src, encoding="utf-8")
    monkeypatch.setenv("JOBDIGEST_CONFIG_DIR", str(tmp_path))
    from jobdigest.config import load_feeds

    with caplog.at_level(logging.WARNING, logger="jobdigest.config"):
        load_feeds()
    assert any("feeds.example.yaml" in r.getMessage() for r in caplog.records)
