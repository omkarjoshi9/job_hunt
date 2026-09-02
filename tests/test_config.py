from pathlib import Path

from jobdigest.config import load_models, load_profile

ROOT = Path(__file__).resolve().parents[1]


def test_profile_filter_types():
    p = load_profile()
    assert p.batch == 2027
    assert isinstance(p.batch, int)
    assert isinstance(p.cgpa, float)
    assert p.digest_max == 12
    assert p.digest_hour == 10
    assert p.timezone == "Asia/Kolkata"
    assert p.work_auth == "India"
    assert "intern" in p.role_types
    assert "new-grad" in p.role_types
    assert "Bengaluru" in p.relocate
    assert p.reject_remote_us is True
    assert p.min_yoe_reject == 3


def test_example_profile_has_no_personal_identity():
    text = (ROOT / "config" / "profile.example.yaml").read_text(encoding="utf-8")
    assert "Omkar" not in text
    assert "8.88" not in text
    assert "PICT" not in text


def test_models_both_keys_not_free():
    m = load_models()
    assert m.score_model
    assert m.summarize_model
    assert not m.score_model.endswith(":free")
    assert not m.summarize_model.endswith(":free")
    assert m.batch_size == 12
