"""End-to-end pipeline tests: collect -> digest -> contacts -> sheet-sync.

Everything external is faked (ATS HTTP via MockTransport, the LLM, Telegram, the
Google worksheet), but the real stages, config loading, database and CLI wiring
run exactly as in .github/workflows/pipeline.yml.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import httpx
import pytest
import yaml

from jobdigest import collect, contacts as _contacts_pkg, digest, score, sheet  # noqa: F401
from jobdigest.contacts.run import run as contacts_run
from jobdigest.openrouter import OpenRouterError
from test_sheet import FakeSheet

ROOT = Path(__file__).resolve().parents[1]


def _gh_job(job_id: int, title: str, location: str, body: str) -> dict:
    return {
        "id": job_id,
        "title": title,
        "location": {"name": location},
        "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{job_id}",
        "content": f"<p>{body}</p>",
        "updated_at": "2026-11-01T00:00:00Z",
        "company_name": "Acme",
    }


GOOD = _gh_job(
    1,
    "Software Engineer Intern",
    "Bengaluru, India",
    "You will build Python backend services. Responsibilities include APIs.",
)
SENIOR = _gh_job(2, "Senior Staff Engineer", "Bengaluru, India", "10+ years experience.")
US_ONLY = _gh_job(3, "Software Engineer Intern", "New York, USA", "Remote US only.")


class Board:
    """A mutable fake Greenhouse board served through httpx.MockTransport."""

    def __init__(self, jobs: list[dict] | None = None, status: int = 200):
        self.jobs = list(jobs if jobs is not None else [GOOD, SENIOR, US_ONLY])
        self.status = status

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            if self.status != 200:
                return httpx.Response(self.status, text="boom")
            return httpx.Response(
                200, json={"jobs": self.jobs, "meta": {"total": len(self.jobs)}}
            )

        return httpx.Client(transport=httpx.MockTransport(handler))


class FakeTelegram:
    def __init__(self):
        self.messages: list[dict] = []

    def __call__(self, url, json=None, timeout=30.0):  # noqa: A002
        self.messages.append({"url": url, **(json or {})})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    @property
    def texts(self) -> list[str]:
        return [str(m.get("text", "")) for m in self.messages]


def fake_llm(messages, *, model, api_key, json_mode=False, **_kw):
    ids = {
        int(x)
        for x in re.findall(r"<<<JOB id=(\d+)>>>", messages[-1]["content"])
    }
    return json.dumps(
        {
            "jobs": [
                {
                    "job_id": i,
                    "score": 88,
                    "relevant": True,
                    "level_ok": True,
                    "location_ok": True,
                    "stack_ok": True,
                    "why": "fits",
                    "kill": None,
                }
                for i in sorted(ids)
            ]
        }
    )


def dead_http_client() -> httpx.Client:
    """Client for contact enrichment: every lookup 404s, nothing leaves the box."""
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "profile.yaml").write_text(
        (ROOT / "config" / "profile.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (cfg / "models.yaml").write_text(
        (ROOT / "config" / "models.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (cfg / "companies.yaml").write_text(
        yaml.safe_dump(
            {
                "companies": [
                    {
                        "id": "acme",
                        "name": "Acme",
                        "ats": "greenhouse",
                        "token": "acme",
                        "enabled": True,
                        "notes": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    for name in ("portals", "feeds", "marketplaces"):
        (cfg / f"{name}.yaml").write_text(f"{name}: []\n", encoding="utf-8")
    monkeypatch.setenv("JOBDIGEST_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("JOBDIGEST_REQUIRE_TELEGRAM", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JOBDIGEST_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    return tmp_path / "jobs.db"


def run_collect(db, board, tg=None, **kw):
    return collect.run(
        db_path=db,
        client=board.client(),
        sleep=lambda _s: None,
        ats_only=True,
        telegram_http_post=tg,
        **kw,
    )


def rows(db, sql, *args):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def test_full_pipeline_happy_path(env, capsys):
    db, tg, board, ws = env, FakeTelegram(), Board(), FakeSheet()

    assert run_collect(db, board, tg) == 0
    jobs = {r["id"]: r for r in rows(db, "SELECT * FROM jobs")}
    assert {j["hard_status"] for j in jobs.values()} == {"pass", "reject"}
    by_reason = {j["title"] + "|" + j["location_raw"]: j["hard_reason"] for j in jobs.values()}
    assert by_reason["Senior Staff Engineer|Bengaluru, India"] == "title_reject"
    assert by_reason["Software Engineer Intern|New York, USA"] == "remote_us"
    assert by_reason["Software Engineer Intern|Bengaluru, India"] == "ok"

    assert (
        digest.run(
            db_path=db,
            score_chat_fn=fake_llm,
            summarize_chat_fn=lambda *a, **k: "",
            telegram_http_post=tg,
        )
        == 0
    )
    scored = rows(db, "SELECT job_id, relevant FROM scores")
    assert len(scored) == 1 and scored[0]["relevant"] == 1  # rejected jobs never hit the LLM
    sent = "\n".join(tg.texts)
    assert "Acme" in sent and "Software Engineer Intern" in sent
    assert "Senior Staff" not in sent
    assert rows(db, "SELECT notify_kind FROM jobs WHERE notified_at IS NOT NULL") == [
        {"notify_kind": "digest"}
    ]

    assert contacts_run(db_path=db, client=dead_http_client()) == 0
    assert sheet.sync(db_path=db, worksheet=ws) == 0
    data = [r for r in ws.rows[1:] if any(r)]
    assert len(data) == 1 and "Acme" in data[0]

    kinds = {r["kind"]: r["status"] for r in rows(db, "SELECT kind, status FROM runs")}
    assert kinds == {
        "collect": "ok",
        "score": "ok",
        "digest": "ok",
        "contacts": "ok",
    } or all(v == "ok" for v in kinds.values()), kinds


def test_second_digest_does_not_resend_notified_jobs(env):
    db, tg, board = env, FakeTelegram(), Board()
    run_collect(db, board, tg)
    kwargs = dict(
        db_path=db,
        score_chat_fn=fake_llm,
        summarize_chat_fn=lambda *a, **k: "",
        telegram_http_post=tg,
    )
    assert digest.run(**kwargs) == 0
    first = len(tg.messages)
    assert first >= 1
    assert digest.run(**kwargs) == 0
    assert len(tg.messages) == first, "already-notified jobs were sent again"


def test_collect_is_idempotent_and_dedupes(env):
    db, board = env, Board()
    assert run_collect(db, board) == 0
    assert run_collect(db, board) == 0
    assert len(rows(db, "SELECT id FROM jobs")) == 3
    assert rows(db, "SELECT COUNT(*) AS n FROM jobs WHERE is_active = 1")[0]["n"] == 3


def test_job_that_leaves_the_board_is_closed_after_two_misses(env):
    db, board = env, Board()
    run_collect(db, board)
    board.jobs = [GOOD]  # SENIOR and US_ONLY disappear
    run_collect(db, board)
    assert rows(db, "SELECT COUNT(*) AS n FROM jobs WHERE is_active = 1")[0]["n"] == 3
    run_collect(db, board)
    active = rows(db, "SELECT title FROM jobs WHERE is_active = 1")
    assert [r["title"] for r in active] == ["Software Engineer Intern"]


def test_collect_total_failure_exits_1_and_alerts(env):
    db, tg = env, FakeTelegram()
    assert run_collect(db, Board(status=500), tg) == 1
    assert any("COLLECT FAILED" in t for t in tg.texts)
    assert rows(db, "SELECT status FROM runs WHERE kind = 'collect'") == [{"status": "error"}]


def test_digest_fails_loud_when_scoring_fails(env):
    db, tg = env, FakeTelegram()
    run_collect(db, Board(), tg)

    def broken(*_a, **_k):
        raise OpenRouterError("boom")

    code = digest.run(
        db_path=db,
        score_chat_fn=broken,
        summarize_chat_fn=lambda *a, **k: "",
        telegram_http_post=tg,
    )
    assert code == 1
    assert any("FAILED" in t.upper() for t in tg.texts)


def test_score_continues_past_a_failed_batch(env, monkeypatch):
    import dataclasses

    from jobdigest.config import load_models

    db = env
    # Distinct titles: identical title/company/location would be merged as duplicates.
    flavours = ["Backend", "Platform", "Data", "Security", "Cloud", "Infrastructure"]
    jobs = [
        _gh_job(10 + n, f"{kind} Software Engineer Intern", "Bengaluru, India", "Python APIs.")
        for n, kind in enumerate(flavours)
    ]
    run_collect(db, Board(jobs))
    small = dataclasses.replace(load_models(), batch_size=2)
    monkeypatch.setattr("jobdigest.score.load_models", lambda: small)
    calls = {"n": 0}

    def flaky(messages, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OpenRouterError("transient")
        return fake_llm(messages, **kw)

    assert score.run(db_path=db, chat_fn=flaky) == 1  # failure is still reported
    assert calls["n"] == 3, "scoring must try every batch, not stop at the first failure"
    assert rows(db, "SELECT COUNT(*) AS n FROM scores")[0]["n"] == 4


def test_digest_without_a_collect_run_fails_loud(env):
    """Guards the workflow: a digest must never run silently on a missing collect."""
    tg = FakeTelegram()
    code = digest.run(
        db_path=env,
        score_chat_fn=fake_llm,
        summarize_chat_fn=lambda *a, **k: "",
        telegram_http_post=tg,
    )
    assert code == 1
    assert any("COLLECT FAILED" in t for t in tg.texts)


def test_cli_contacts_and_sheet_sync_run_offline(env, capsys):
    from jobdigest.cli import main

    assert main(["contacts", "--db", str(env)]) == 0
    assert main(["sheet-sync", "--db", str(env)]) == 0  # no credentials: clean skip
    assert "skip" in capsys.readouterr().out


# --- workflow wiring (structure, not substrings) ---------------------------------


def _workflow():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8"))


def _step_runs(job: dict) -> list[str]:
    return [s.get("run", "") for s in job["steps"]]


def test_pipeline_workflow_stage_order_and_gating():
    wf = _workflow()
    job = wf["jobs"]["pipeline"]
    runs = _step_runs(job)
    order = [
        next(i for i, r in enumerate(runs) if f"jobdigest {stage}" in r)
        for stage in ("collect", "digest", "contacts", "sheet-sync")
    ]
    assert order == sorted(order), "stages must run collect -> digest -> contacts -> sheet-sync"
    assert "gate" in str(job.get("needs", "")), "pipeline job must depend on the gate job"
    assert "due" in str(job.get("if", "")), "pipeline job must be gated on the cron slot"


def test_pipeline_workflow_provides_every_secret_the_code_requires():
    text = (ROOT / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    for secret in (
        "DATABASE_URL",
        "OPENROUTER_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "GOOGLE_SHEET_ID",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "PROFILE_YAML",
        "COMPANIES_YAML",
        "MODELS_YAML",
    ):
        assert f"secrets.{secret}" in text, secret


# --- discovered boards ------------------------------------------------------------


def test_collect_includes_discovered_boards_without_duplicating_curated(env, monkeypatch, tmp_path):
    tsv = tmp_path / "boards_india.tsv"
    tsv.write_text(
        "company\tats\ttoken\tcandidates\tsample\n"
        "Acme Again\tgreenhouse\tacme\t1\tdup of curated\n"
        "Newco\tgreenhouse\tnewco\t2\tSoftware Engineer Intern\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBDIGEST_DISCOVERED_BOARDS", str(tsv))
    seen: list[str] = []

    def fetch_fn(company, client, sleep):
        from jobdigest.ats.base import FetchResult, FetchStatus

        seen.append(f"{company.ats}:{company.token}")
        return FetchResult(FetchStatus.EMPTY_BOARD, 200, [])

    code = collect.run(
        db_path=env, client=Board().client(), sleep=lambda _s: None,
        fetch_fn=fetch_fn, ats_only=True,
    )
    assert code == 0
    assert sorted(seen) == ["greenhouse:acme", "greenhouse:newco"]  # acme fetched once


def test_skip_discovered_flag_leaves_only_curated_boards(env, monkeypatch, tmp_path):
    tsv = tmp_path / "b.tsv"
    tsv.write_text("company\tats\ttoken\nNewco\tgreenhouse\tnewco\n", encoding="utf-8")
    monkeypatch.setenv("JOBDIGEST_DISCOVERED_BOARDS", str(tsv))
    seen: list[str] = []

    def fetch_fn(company, client, sleep):
        from jobdigest.ats.base import FetchResult, FetchStatus

        seen.append(company.token)
        return FetchResult(FetchStatus.EMPTY_BOARD, 200, [])

    collect.run(db_path=env, client=Board().client(), sleep=lambda _s: None,
                fetch_fn=fetch_fn, ats_only=True, skip_discovered=True)
    assert seen == ["acme"]


def test_pipeline_keeps_state_between_runs_even_without_a_database_secret():
    """Without this the digest forgets what it sent and repeats the same companies."""
    job = _workflow()["jobs"]["pipeline"]
    assert job["env"]["DATABASE_PATH"].endswith(".db")
    steps = job["steps"]
    restore = next(i for i, s in enumerate(steps) if "cache/restore" in s.get("uses", ""))
    collect = next(i for i, s in enumerate(steps) if "jobdigest collect" in s.get("run", ""))
    save = next(i for i, s in enumerate(steps) if "cache/save" in s.get("uses", ""))
    assert restore < collect < save
    assert steps[save]["if"] == "always()", "state must be saved even if a later step fails"
    assert steps[restore]["with"]["path"] == steps[save]["with"]["path"]
    assert "restore-keys" in steps[restore]["with"], "must fall back to the latest saved state"
