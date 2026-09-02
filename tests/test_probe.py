from __future__ import annotations

import json
from pathlib import Path

import httpx
import yaml

from jobdigest.probe import main

ROOT = Path(__file__).resolve().parents[1]
REPO_YAML = ROOT / "config" / "companies.example.yaml"
GH_TWO = ROOT / "tests" / "fixtures" / "ats" / "greenhouse" / "two_jobs.json"
GH_EMPTY = ROOT / "tests" / "fixtures" / "ats" / "greenhouse" / "empty.json"
GH_404 = ROOT / "tests" / "fixtures" / "ats" / "greenhouse" / "not_found.json"


def _write_companies(path: Path) -> None:
    data = {
        "companies": [
            {
                "id": "deadco",
                "name": "Dead Co",
                "ats": "greenhouse",
                "token": "dead",
                "enabled": False,
                "notes": "",
            },
            {
                "id": "emptyco",
                "name": "Empty Co",
                "ats": "greenhouse",
                "token": "empty",
                "enabled": False,
                "notes": "",
            },
            {
                "id": "okco",
                "name": "Ok Co",
                "ats": "greenhouse",
                "token": "oktoken",
                "enabled": False,
                "notes": "",
            },
            {
                "id": "peakxv",
                "name": "Peak XV",
                "ats": "getro",
                "token": "peakxv",
                "enabled": False,
                "notes": "unimplemented in I2; adapter must not return empty_board",
            },
        ]
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_probe_mock_status_and_does_not_write_yaml(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "companies.yaml"
    _write_companies(cfg)
    before_tmp = cfg.read_bytes()
    before_repo = REPO_YAML.read_bytes()
    monkeypatch.setenv("JOBDIGEST_CONFIG_DIR", str(tmp_path))

    http_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        http_calls["n"] += 1
        path = request.url.path
        if "/boards/dead/" in path:
            return httpx.Response(404, json=json.loads(GH_404.read_text(encoding="utf-8")))
        if "/boards/empty/" in path:
            return httpx.Response(200, json=json.loads(GH_EMPTY.read_text(encoding="utf-8")))
        if "/boards/oktoken/" in path:
            return httpx.Response(200, json=json.loads(GH_TWO.read_text(encoding="utf-8")))
        return httpx.Response(500, text="unexpected")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        code = main(argv=[], client=client, sleep=lambda _: None)

    assert code == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert any(line.startswith("deadco\t") and "\tdead_token\t" in line for line in lines)
    assert any(line.startswith("emptyco\t") and "\tempty_board\t" in line for line in lines)
    assert any(line.startswith("okco\t") and "\tok\t" in line for line in lines)
    assert "peakxv\tgetro\tunimplemented" in out
    assert "empty_board" not in [line.split("\t")[-1] for line in lines if line.startswith("peakxv\t")]
    assert http_calls["n"] == 3
    assert cfg.read_bytes() == before_tmp
    assert REPO_YAML.read_bytes() == before_repo


def test_all_seed_companies_disabled():
    path = ROOT / "config" / "companies.example.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = data["companies"]
    assert rows
    for row in rows:
        assert row["enabled"] is False
    hf = next(row for row in rows if row["id"] == "huggingface")
    assert hf["ats"] == "workable"
    assert hf["enabled"] is False
    unimplemented = {row["id"]: row["ats"] for row in rows if row["ats"] in ("getro", "workday")}
    assert unimplemented["peakxv"] == "getro"
    assert unimplemented["surge"] == "getro"
    assert unimplemented["nexusvp"] == "getro"
    assert unimplemented["flipkart"] == "workday"
    assert unimplemented["fractal"] == "workday"
