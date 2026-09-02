from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "jobdigest"
FORBIDDEN = (
    "StealthyFetcher",
    "DynamicFetcher",
    "FetcherSession",
    "DynamicSession",
    "StealthySession",
)
OUTSIDE = ("collect.py", "score.py", "digest.py", "cli.py", "feeds.py")


def test_fetcher_class_names_only_under_portals():
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "portals" in path.parts:
            assert "StealthyFetcher" not in text, path
            assert "DynamicFetcher" not in text, path
            continue
        for name in FORBIDDEN:
            assert name not in text, f"{path} contains {name}"


def test_pipeline_modules_have_no_scrapling_sessions():
    for name in OUTSIDE:
        text = (SRC / name).read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in text, f"{name} contains {token}"
        assert "scrapling" not in text.lower(), name
