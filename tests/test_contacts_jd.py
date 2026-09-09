from pathlib import Path

from jobdigest.contacts.jd import parse_jd_names

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contacts"


def test_parse_jd_hiring_manager():
    text = (FIXTURES / "jd_hiring_manager.txt").read_text(encoding="utf-8")
    found = parse_jd_names(text)
    names = [c.name for c in found]
    assert "Priya Sharma" in names
    priya = next(c for c in found if c.name == "Priya Sharma")
    assert priya.source == "jd"
    assert priya.role == "Hiring Manager"
    assert priya.confidence == 100.0


def test_parse_jd_recruiter_and_rejects_degree_false_positive():
    text = (FIXTURES / "jd_recruiter_and_degree.txt").read_text(encoding="utf-8")
    found = parse_jd_names(text)
    names = [c.name for c in found]
    assert "Arun Mehta" in names
    assert "Kavita Rao" in names
    assert all("Science" not in c.name for c in found)
    assert all("Computer" not in c.name for c in found)
    arun = next(c for c in found if c.name == "Arun Mehta")
    assert arun.role == "Recruiter"
    assert arun.confidence == 90.0


def test_parse_jd_empty():
    assert parse_jd_names("") == []
    assert parse_jd_names(None) == []
