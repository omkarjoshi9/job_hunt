from pathlib import Path

import httpx

from jobdigest.config import load_profile
from jobdigest.contacts.pipeline import enrich_job, run_pipeline
from jobdigest.contacts.rank import merge_contacts
from jobdigest.contacts.types import DiscoveredContact
from jobdigest.db import connect, last_ok_run, upsert_job
from jobdigest.contacts.run import run as contacts_run

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "jobdigest" / "contacts"


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "autocomplete.clearbit.com" in url:
        return httpx.Response(200, json=[{"name": "Acme", "domain": "acme.com"}])
    if "/search/commits" in url:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "commit": {
                            "author": {"email": "priya.sharma@acme.com"},
                            "committer": {"email": "noreply@github.com"},
                        }
                    }
                ]
            },
        )
    if request.url.path.endswith("/orgs/acme/members"):
        return httpx.Response(200, json=[{"login": "talent-jane"}])
    if request.url.path.endswith("/users/talent-jane"):
        return httpx.Response(
            200,
            json={
                "login": "talent-jane",
                "name": "Jane Talent",
                "bio": "Talent partner",
                "email": None,
            },
        )
    if (request.url.host or "") == "acme.com":
        return httpx.Response(200, text="ok")
    return httpx.Response(404, text="no")


def test_run_pipeline_never_raises_and_merges():
    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        found = run_pipeline(
            company="Acme",
            description_text="Reports to: Priya Sharma. Recruiter: Arun Mehta.",
            client=client,
            github_token_value="pat",
        )
    names = {c.name: c for c in found}
    assert "Priya Sharma" in names
    assert names["Priya Sharma"].source == "jd"
    assert names["Priya Sharma"].email == "priya.sharma@acme.com"
    # email is constructed from the pattern, so confidence is capped
    assert names["Priya Sharma"].confidence == 40.0
    assert names["Priya Sharma"].email_inferred is True
    assert "Jane Talent" in names
    assert names["Jane Talent"].source == "github"
    assert names["Jane Talent"].confidence == 40.0  # constructed email -> capped
    assert names["Arun Mehta"].confidence == 40.0


def test_run_pipeline_swallows_http_errors():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    transport = httpx.MockTransport(boom)
    with httpx.Client(transport=transport) as client:
        found = run_pipeline(
            company="Acme",
            description_text="Reports to: Priya Sharma.",
            client=client,
            github_token_value="pat",
        )
    assert [c.name for c in found] == ["Priya Sharma"]


def test_merge_attaches_constructed_email_onto_jd():
    merged = merge_contacts(
        jd=[
            DiscoveredContact(
                name="Priya Sharma", source="jd", role="Hiring Manager", confidence=100
            )
        ],
        github=[],
        company="Acme",
        domain="acme.com",
        pattern="{first}.{last}@acme.com",
    )
    assert merged[0].email == "priya.sharma@acme.com"
    assert merged[0].source == "jd"


def test_contacts_package_has_no_banned_integrations():
    blob = ""
    for path in PKG.rglob("*.py"):
        blob += path.read_text(encoding="utf-8").lower()
    assert "playwright" not in blob
    assert "linkedin.com" not in blob
    assert "smtplib" not in blob
    assert "openrouter" not in blob
    assert "hunter.io" not in blob
    assert "os.environ.get(\"github_token\")" not in blob
    assert "os.environ.get('github_token')" not in blob


def _plant_job(conn, *, url="https://example.com/jobs/c1"):
    profile = load_profile()
    action, jid = upsert_job(
        conn,
        profile,
        apply_url=url,
        hosted_url=url,
        source="greenhouse",
        external_id=url.rsplit("/", 1)[-1],
        company_id=None,
        company="Acme Inc.",
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="Reports to: Priya Sharma. Intern role in Pune.",
        raw_json="{}",
        ats="greenhouse",
        employment_hint="intern",
    )
    assert action == "insert" and jid is not None
    return jid


def test_contacts_run_uses_job_ids_and_exit_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("JOBDIGEST_GITHUB_TOKEN", raising=False)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        jid = _plant_job(conn)
    finally:
        conn.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if "autocomplete.clearbit.com" in str(request.url):
            return httpx.Response(200, json=[{"domain": "acme.com"}])
        return httpx.Response(404, text="no")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        code = contacts_run(db_path=db, job_ids=[jid], client=client)
    assert code == 0
    conn = connect(db)
    try:
        row = last_ok_run(conn, "contacts")
        assert row is not None
        stats = __import__("json").loads(row["stats_json"])
        assert stats["github"] == "skip"
        assert stats["jd_hits"] >= 1
        assert stats["jobs"] == 1
        names = [
            r["name"]
            for r in conn.execute("SELECT name FROM contacts WHERE job_id = ?", (jid,)).fetchall()
        ]
        assert "Priya Sharma" in names
    finally:
        conn.close()


def test_enrich_job_reads_description():
    transport = httpx.MockTransport(_handler)
    with httpx.Client(transport=transport) as client:
        found = enrich_job(
            {
                "company": "Acme",
                "description_text": "Hiring manager: Priya Sharma",
            },
            client=client,
            github_token_value="",
        )
    assert any(c.name == "Priya Sharma" for c in found)


def test_merge_observed_email_keeps_full_confidence_constructed_is_capped():
    merged = merge_contacts(
        jd=[
            DiscoveredContact(name="Priya Sharma", source="jd", role="Hiring Manager", confidence=100),
            DiscoveredContact(
                name="Arun Mehta", source="jd", role="Recruiter", email="arun@acme.com", confidence=90
            ),
        ],
        github=[],
        company="Acme",
        domain="acme.com",
        pattern="{first}.{last}@acme.com",
    )
    by = {c.name: c for c in merged}
    assert by["Arun Mehta"].confidence == 90.0 and not by["Arun Mehta"].email_inferred
    assert by["Priya Sharma"].confidence == 40.0 and by["Priya Sharma"].email_inferred


def test_merge_without_pattern_has_no_email_and_full_confidence():
    merged = merge_contacts(
        jd=[DiscoveredContact(name="Priya Sharma", source="jd", confidence=100)],
        github=[],
        company="Acme",
        domain=None,
        pattern=None,
    )
    assert merged[0].email is None and merged[0].confidence == 100.0
