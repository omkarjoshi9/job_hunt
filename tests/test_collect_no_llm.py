from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_collect_source_has_no_openrouter():
    text = (ROOT / "src" / "jobdigest" / "collect.py").read_text(encoding="utf-8").lower()
    assert "openrouter" not in text
    assert "openai" not in text
    assert "scrapling" not in text
    assert "gspread" not in text
    assert "jobdigest_enable" not in text


def test_score_digest_source_has_no_gspread():
    for name in ("score.py", "digest.py"):
        text = (ROOT / "src" / "jobdigest" / name).read_text(encoding="utf-8").lower()
        assert "gspread" not in text, name
