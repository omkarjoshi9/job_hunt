from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_src_has_no_stealthy_or_dynamic_fetcher():
    src = ROOT / "src" / "jobdigest"
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "StealthyFetcher" not in text, path
        assert "DynamicFetcher" not in text, path
