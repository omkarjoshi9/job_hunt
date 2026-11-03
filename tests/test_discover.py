from __future__ import annotations

import os
from pathlib import Path

import yaml

from jobdigest.discover import (
    Skip,
    Unresolved,
    annotate_row,
    diff_tokens,
    extract_token,
    format_suggested_yaml,
    india_location,
    main,
    parse_intern_markdown,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "discover"
SAMPLE_MD = FIXTURES / "intern_intl_sample.md"
MINI_YAML = FIXTURES / "companies_mini.yaml"
WF = ROOT / ".github" / "workflows"


def _sample_text() -> str:
    return SAMPLE_MD.read_text(encoding="utf-8")


def _mini_payload() -> dict:
    return yaml.safe_load(MINI_YAML.read_text(encoding="utf-8"))


def _india_extracted():
    rows = [r for r in parse_intern_markdown(_sample_text()) if india_location(r.location)]
    classified = [annotate_row(row) for row in rows]
    extracted = [row for row in classified if row.ats and row.token]
    unresolved = [
        row for row in classified if row.skip_reason == Unresolved().reason
    ]
    return extracted, unresolved


def test_india_filter_keeps_bengaluru_drops_canada():
    assert india_location("Bengaluru") is True
    assert india_location("bangalore") is True
    assert india_location("Remote, India") is True
    assert india_location("Canada") is False
    assert india_location("Toronto, Canada") is False
    rows = parse_intern_markdown(_sample_text())
    names = {r.company_name for r in rows if india_location(r.location)}
    assert "CloudSEK" in names
    assert "Rivian" not in names
    assert "NVIDIA" not in names
    assert "IgnoreMe" not in {r.company_name for r in rows}


def test_extract_greenhouse_job_boards_cloudsek():
    assert extract_token(
        "https://job-boards.greenhouse.io/cloudsek/jobs/6149788004"
    ) == ("greenhouse", "cloudsek")
    assert extract_token("https://boards.greenhouse.io/boardsgh/jobs/1234567") == (
        "greenhouse",
        "boardsgh",
    )


def test_extract_lever_merklescience():
    assert extract_token(
        "https://jobs.lever.co/merklescience/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ) == ("lever", "merklescience")


def test_extract_ashby_keeps_dot_token():
    assert extract_token(
        "https://jobs.ashbyhq.com/rivianvw.tech/0f1e2d3c-4b5a-6978-90ab-cdef12345678"
    ) == ("ashby", "rivianvw.tech")
    assert extract_token(
        "https://jobs.ashbyhq.com/acme.india/bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    ) == ("ashby", "acme.india")


def test_skip_workday():
    result = extract_token(
        "https://salesforce.wd12.myworkdayjobs.com/en-US/External_Career_Site/job/Bangalore/Intern"
    )
    assert isinstance(result, Skip)
    assert result.reason == "workday"
    nvidia = extract_token("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
    assert isinstance(nvidia, Skip)
    assert nvidia.reason == "workday"


def test_gh_jid_without_board_is_unresolved():
    result = extract_token("https://stripe.com/jobs/search?gh_jid=7218889")
    assert isinstance(result, Unresolved)
    assert result.reason == "greenhouse_embed_unresolved"


def test_diff_marks_mactores_known_cloudsek_new():
    extracted, unresolved = _india_extracted()
    diff = diff_tokens(extracted, _mini_payload(), unresolved=unresolved)
    known_names = {r.company_name.casefold() for r in diff.known}
    new_by_id = {row["id"]: row for row in diff.new}
    assert "mactores" in known_names
    assert "stripe" in known_names
    assert "cloudsek" in new_by_id
    assert new_by_id["cloudsek"]["ats"] == "greenhouse"
    assert new_by_id["cloudsek"]["token"] == "cloudsek"
    assert new_by_id["cloudsek"]["enabled"] is False
    assert "merklescience" in new_by_id
    assert new_by_id["merklescience"]["ats"] == "lever"
    assert "ignoreme" not in new_by_id
    assert all(row["id"] != "stripe" for row in diff.new)
    assert all(row["ats"] != "workday" for row in diff.new)


def test_suggested_yaml_enabled_false_six_keys():
    rows = [
        {
            "id": "cloudsek",
            "name": "CloudSEK",
            "ats": "greenhouse",
            "token": "cloudsek",
            "enabled": True,
            "notes": "INTERN_INTL.md Bengaluru; probe before enable",
            "extra": "drop-me",
        }
    ]
    text = format_suggested_yaml(rows)
    assert "enabled: false" in text
    assert "enabled: true" not in text
    data = yaml.safe_load(text)
    assert list(data["companies"][0].keys()) == [
        "id",
        "name",
        "ats",
        "token",
        "enabled",
        "notes",
    ]
    assert data["companies"][0]["enabled"] is False
    assert data["companies"][0]["id"] == "cloudsek"


def test_discover_does_not_write_companies_path(tmp_path, capsys):
    dest = tmp_path / "companies.yaml"
    dest.write_bytes(MINI_YAML.read_bytes())
    before = dest.read_bytes()
    before_mini = MINI_YAML.read_bytes()
    example = ROOT / "config" / "companies.example.yaml"
    before_example = example.read_bytes()
    hunt = ROOT / "config" / "companies.yaml"
    before_hunt = hunt.read_bytes() if hunt.is_file() else None
    hosted = ROOT / "config" / "companies.hosted.yaml"
    before_hosted = hosted.read_bytes() if hosted.is_file() else None
    research = ROOT / "config" / "research_ats_boards.yaml"
    before_research = research.read_bytes() if research.is_file() else None

    code = main(
        argv=["--markdown", str(SAMPLE_MD), "--companies", str(dest)],
        client=None,
    )
    capsys.readouterr()
    assert code == 0
    assert dest.read_bytes() == before
    assert MINI_YAML.read_bytes() == before_mini
    assert example.read_bytes() == before_example
    if before_hunt is not None:
        assert hunt.read_bytes() == before_hunt
    if before_hosted is not None:
        assert hosted.read_bytes() == before_hosted
    if before_research is not None:
        assert research.read_bytes() == before_research


def test_non_india_ashby_not_extracted():
    rows = parse_intern_markdown(_sample_text())
    india = [r for r in rows if india_location(r.location)]
    extracted_keys = set()
    for row in india:
        result = extract_token(row.apply_url)
        if isinstance(result, tuple):
            extracted_keys.add((result[0], result[1]))
    assert ("ashby", "rivianvw.tech") not in extracted_keys
    assert not any(r.company_name.casefold() == "rivian" for r in india)
    assert ("ashby", "acme.india") in extracted_keys


def test_probe_overlay_calls_probe_with_new_ids_only(tmp_path, monkeypatch, capsys):
    dest = tmp_path / "companies.yaml"
    dest.write_bytes(MINI_YAML.read_bytes())
    before = dest.read_bytes()
    seen: dict = {}

    def fake_probe(argv=None, client=None, sleep=None):
        seen["argv"] = list(argv or [])
        seen["cfg"] = os.environ.get("JOBDIGEST_CONFIG_DIR")
        cfg_dir = Path(seen["cfg"])
        overlay = cfg_dir / "companies.yaml"
        seen["overlay_exists"] = overlay.is_file()
        payload = yaml.safe_load(overlay.read_text(encoding="utf-8"))
        seen["overlay_rows"] = payload["companies"]
        print("cloudsek\tgreenhouse\tcloudsek\t200\tok\t1")
        return 0

    monkeypatch.setattr("jobdigest.probe.main", fake_probe)
    before_env = os.environ.get("JOBDIGEST_CONFIG_DIR")
    code = main(
        argv=["--markdown", str(SAMPLE_MD), "--companies", str(dest), "--probe"],
        client=None,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert dest.read_bytes() == before
    assert os.environ.get("JOBDIGEST_CONFIG_DIR") == before_env
    assert seen["argv"][0] == "--ids"
    ids = {part for part in seen["argv"][1].split(",") if part}
    assert "cloudsek" in ids
    assert "merklescience" in ids
    assert "mactores" not in ids
    assert "stripe" not in ids
    assert seen["overlay_exists"] is True
    assert Path(seen["cfg"]) != dest.parent
    for row in seen["overlay_rows"]:
        if row["id"] in ids:
            assert row["enabled"] is False
    assert "## probe" in out
    assert "## suggested yaml" in out
    yaml_block = out.split("## suggested yaml", 1)[1]
    assert "enabled: false" in yaml_block
    assert "enabled: true" not in yaml_block
    assert yaml_block.find("companies:") >= 0


def test_workflows_pipeline_has_no_discover():
    pipeline = (WF / "pipeline.yml").read_text(encoding="utf-8")
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    ci = (WF / "ci.yml").read_text(encoding="utf-8")
    assert "jobdigest.discover" not in pipeline
    assert "jobdigest.discover" not in start
    assert "jobdigest.discover" not in ci


def test_url_without_fetch_rejected_even_with_markdown(capsys):
    code = main(argv=["--markdown", str(SAMPLE_MD), "--url", "http://x/y.md"])
    assert code == 1
    assert "--url requires --fetch" in capsys.readouterr().err


def test_url_without_fetch_or_markdown_rejected(capsys):
    assert main(argv=["--url", "http://x/y.md"]) == 1
    assert "--url requires --fetch" in capsys.readouterr().err


def test_probe_failure_restores_config_env(tmp_path, monkeypatch):
    dest = tmp_path / "companies.yaml"
    dest.write_bytes(MINI_YAML.read_bytes())
    monkeypatch.setenv("JOBDIGEST_CONFIG_DIR", "/orig")

    def bad_probe(argv=None, client=None, sleep=None):
        raise RuntimeError("probe died")

    monkeypatch.setattr("jobdigest.probe.main", bad_probe)
    try:
        main(argv=["--markdown", str(SAMPLE_MD), "--companies", str(dest), "--probe"])
    except RuntimeError:
        pass
    assert os.environ["JOBDIGEST_CONFIG_DIR"] == "/orig"
