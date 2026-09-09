import json
import sqlite3
from datetime import datetime, timezone
from jobdigest.ats.base import FetchResult, FetchStatus, NormalizedJob
from jobdigest.collect import run
from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    bump_unseen_for_source,
    connect,
    guest_rows,
    note_seen_job,
    upsert_job,
    upsert_score,
)
from jobdigest.portals import PortalResult


def _intern_job(*, url: str, ext: str, source: str = "example") -> NormalizedJob:
    return NormalizedJob(
        title="Software Engineer Intern",
        company="Example Corp",
        location_raw="Pune",
        apply_url=url,
        hosted_url=url,
        employment_hint="intern",
        workplace_hint=None,
        description_text="Intern role in Pune",
        source=source,
        ats="greenhouse",
        external_id=ext,
        raw_json="{}",
        fetched_at=datetime.now(timezone.utc),
    )


def _enabled_companies(source_id: str = "example"):
    return {
        "companies": [
            {
                "id": source_id,
                "name": "Example Corp",
                "ats": "greenhouse",
                "token": "example",
                "enabled": True,
            }
        ]
    }


def _empty_portals():
    return {"portals": []}


def _empty_feeds():
    return {"feeds": []}


def _fetch(jobs):
    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, list(jobs))

    return fetch_fn


def _row(conn, source: str = "example"):
    return conn.execute(
        "SELECT * FROM jobs WHERE source = ? ORDER BY id",
        (source,),
    ).fetchone()


def test_migrate_adds_tombstone_columns(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE jobs (
          id INTEGER PRIMARY KEY,
          apply_url TEXT,
          apply_url_norm TEXT NOT NULL UNIQUE,
          source TEXT NOT NULL,
          external_id TEXT,
          company_id INTEGER,
          company TEXT,
          title TEXT NOT NULL,
          location_raw TEXT,
          description_text TEXT,
          raw_json TEXT NOT NULL,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          is_active INTEGER NOT NULL DEFAULT 1,
          hard_status TEXT NOT NULL DEFAULT 'pending',
          hard_reason TEXT,
          ats TEXT NOT NULL DEFAULT ''
        );
        """
    )
    raw.close()
    conn = connect(db)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        assert "miss_count" in cols
        assert "closed_at" in cols
        assert "close_reason" in cols
    finally:
        conn.close()


def test_two_successful_absences_tombstone(monkeypatch, tmp_path):
    job = _intern_job(url="https://example.com/jobs/1", ext="1")
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _empty_portals)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"

    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([job])) == 0
    conn = connect(db)
    try:
        row = _row(conn)
        assert int(row["is_active"]) == 1
        assert int(row["miss_count"]) == 0
        assert row["close_reason"] is None
    finally:
        conn.close()

    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([])) == 0
    conn = connect(db)
    try:
        row = _row(conn)
        assert int(row["is_active"]) == 1
        assert int(row["miss_count"]) == 1
    finally:
        conn.close()

    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([])) == 0
    conn = connect(db)
    try:
        row = _row(conn)
        assert int(row["is_active"]) == 0
        assert int(row["miss_count"]) == 2
        assert row["close_reason"] == "unseen_board"
        assert row["closed_at"]
        run_row = conn.execute(
            "SELECT stats_json FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        stats = json.loads(run_row["stats_json"])
        assert stats["tombstoned"] == 1
        assert stats["missed"] == 1
    finally:
        conn.close()


def test_empty_board_two_runs_closes(monkeypatch, tmp_path):
    job = _intern_job(url="https://example.com/jobs/e", ext="e")
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _empty_portals)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([job])) == 0

    def empty_board(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.EMPTY_BOARD, 200, [])

    assert run(db_path=db, sleep=lambda _: None, fetch_fn=empty_board) == 0
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=empty_board) == 0
    conn = connect(db)
    try:
        row = _row(conn)
        assert int(row["is_active"]) == 0
        assert row["close_reason"] == "unseen_board"
    finally:
        conn.close()


def test_error_does_not_increment_miss(monkeypatch, tmp_path):
    job = _intern_job(url="https://example.com/jobs/err", ext="err")
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _empty_portals)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([job])) == 0

    def boom(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.ERROR, 500, [], error="boom")

    assert run(db_path=db, sleep=lambda _: None, fetch_fn=boom) == 1
    conn = connect(db)
    try:
        row = _row(conn)
        assert int(row["is_active"]) == 1
        assert int(row["miss_count"]) == 0
    finally:
        conn.close()


def test_dead_token_does_not_increment_miss(monkeypatch, tmp_path):
    job = _intern_job(url="https://example.com/jobs/dead", ext="dead")
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _empty_portals)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([job])) == 0

    def dead(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.DEAD_TOKEN, 404, [])

    assert run(db_path=db, sleep=lambda _: None, fetch_fn=dead) == 0
    conn = connect(db)
    try:
        row = _row(conn)
        assert int(row["is_active"]) == 1
        assert int(row["miss_count"]) == 0
    finally:
        conn.close()


def test_incomplete_ok_does_not_sweep(monkeypatch, tmp_path):
    job = _intern_job(url="https://example.com/jobs/inc", ext="inc")
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _empty_portals)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([job])) == 0

    def truncated(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, [], complete=False)

    assert run(db_path=db, sleep=lambda _: None, fetch_fn=truncated) == 0
    conn = connect(db)
    try:
        row = _row(conn)
        assert int(row["is_active"]) == 1
        assert int(row["miss_count"]) == 0
    finally:
        conn.close()


def test_reopen_resets_miss_keeps_first_seen(monkeypatch, tmp_path):
    job = _intern_job(url="https://example.com/jobs/re", ext="re")
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _empty_portals)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([job])) == 0
    conn = connect(db)
    try:
        first_seen = _row(conn)["first_seen_at"]
    finally:
        conn.close()
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([])) == 0
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([])) == 0
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([job])) == 0
    conn = connect(db)
    try:
        row = _row(conn)
        assert int(row["is_active"]) == 1
        assert int(row["miss_count"]) == 0
        assert row["closed_at"] is None
        assert row["close_reason"] is None
        assert row["first_seen_at"] == first_seen
    finally:
        conn.close()


def test_tombstoned_job_leaves_guest_rows(monkeypatch, tmp_path):
    job = _intern_job(url="https://example.com/jobs/guest", ext="guest")
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _empty_portals)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([job])) == 0
    models = load_models()
    conn = connect(db)
    try:
        jid = int(_row(conn)["id"])
        upsert_score(
            conn,
            jid,
            models.score_model,
            models.score_prompt_version,
            True,
            90.0,
            {"why": "fit"},
        )
        assert guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=load_profile(),
        )
    finally:
        conn.close()
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([])) == 0
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([])) == 0
    conn = connect(db)
    try:
        assert (
            guest_rows(
                conn,
                models.score_model,
                models.score_prompt_version,
                12,
                profile=load_profile(),
            )
            == []
        )
    finally:
        conn.close()


def test_portal_zero_cards_does_not_bump(monkeypatch, tmp_path):
    job = _intern_job(
        url="https://portal.example/jobs/1", ext="p1", source="dup-portal"
    )
    monkeypatch.setattr(
        "jobdigest.collect.load_companies",
        lambda: {"companies": []},
    )
    monkeypatch.setattr(
        "jobdigest.collect.load_portals",
        lambda: {
            "portals": [
                {
                    "name": "dup-portal",
                    "url": "https://dup.example/board",
                    "enabled": True,
                    "parser": "generic_cards",
                }
            ]
        },
    )
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"

    def ok_portal(row):
        _ = row
        return PortalResult(status="ok", jobs=[job], http_status=200, name="dup-portal")

    assert run(
        db_path=db,
        sleep=lambda _: None,
        skip_ats=True,
        portal_run_fn=ok_portal,
    ) == 0

    def zero_cards(row):
        _ = row
        return PortalResult(
            status="error",
            jobs=[],
            error_class="parse",
            detail="0 cards",
            name="dup-portal",
            url="https://dup.example/board",
        )

    assert run(
        db_path=db,
        sleep=lambda _: None,
        skip_ats=True,
        portal_run_fn=zero_cards,
    ) == 1
    conn = connect(db)
    try:
        row = _row(conn, "dup-portal")
        assert int(row["is_active"]) == 1
        assert int(row["miss_count"]) == 0
    finally:
        conn.close()


def test_empty_rss_two_ok_fetches_closes(monkeypatch, tmp_path):
    job = _intern_job(url="https://feed.example/jobs/1", ext="f1", source="dup-feed")
    monkeypatch.setattr(
        "jobdigest.collect.load_companies",
        lambda: {"companies": []},
    )
    monkeypatch.setattr("jobdigest.collect.load_portals", _empty_portals)
    monkeypatch.setattr(
        "jobdigest.collect.load_feeds",
        lambda: {
            "feeds": [
                {"name": "dup-feed", "url": "https://dup.example/rss", "enabled": True}
            ]
        },
    )
    db = tmp_path / "jobs.db"

    def ok_feed(row):
        _ = row
        return PortalResult(status="ok", jobs=[job], http_status=200, name="dup-feed")

    def empty_feed(row):
        _ = row
        return PortalResult(status="ok", jobs=[], http_status=200, name="dup-feed")

    assert run(db_path=db, sleep=lambda _: None, skip_ats=True, feed_run_fn=ok_feed) == 0
    assert run(db_path=db, sleep=lambda _: None, skip_ats=True, feed_run_fn=empty_feed) == 0
    assert run(db_path=db, sleep=lambda _: None, skip_ats=True, feed_run_fn=empty_feed) == 0
    conn = connect(db)
    try:
        row = _row(conn, "dup-feed")
        assert int(row["is_active"]) == 0
        assert row["close_reason"] == "unseen_board"
    finally:
        conn.close()


def test_error_on_one_board_does_not_block_other_sweep(monkeypatch, tmp_path):
    def companies():
        return {
            "companies": [
                {
                    "id": "keep",
                    "name": "Keep",
                    "ats": "greenhouse",
                    "token": "keep",
                    "enabled": True,
                },
                {
                    "id": "boom",
                    "name": "Boom",
                    "ats": "greenhouse",
                    "token": "boom",
                    "enabled": True,
                },
            ]
        }

    keep_job = _intern_job(url="https://keep.example/j/1", ext="k1", source="keep")
    monkeypatch.setattr("jobdigest.collect.load_companies", companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _empty_portals)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"

    def first(company, client, sleep):
        _ = client, sleep
        if company.id == "keep":
            return FetchResult(FetchStatus.OK, 200, [keep_job])
        return FetchResult(FetchStatus.OK, 200, [])

    assert run(db_path=db, sleep=lambda _: None, fetch_fn=first) == 0

    def second(company, client, sleep):
        _ = client, sleep
        if company.id == "keep":
            return FetchResult(FetchStatus.EMPTY_BOARD, 200, [])
        return FetchResult(FetchStatus.ERROR, 500, [], error="boom")

    assert run(db_path=db, sleep=lambda _: None, fetch_fn=second) == 0
    conn = connect(db)
    try:
        row = _row(conn, "keep")
        assert int(row["miss_count"]) == 1
        assert int(row["is_active"]) == 1
    finally:
        conn.close()


def test_disabled_company_is_not_swept(monkeypatch, tmp_path):
    job = _intern_job(url="https://example.com/jobs/dis", ext="dis")
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr("jobdigest.collect.load_portals", _empty_portals)
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([job])) == 0

    def disabled():
        payload = _enabled_companies()
        payload["companies"][0]["enabled"] = False
        return payload

    monkeypatch.setattr("jobdigest.collect.load_companies", disabled)
    assert run(db_path=db, sleep=lambda _: None, fetch_fn=_fetch([])) == 0
    conn = connect(db)
    try:
        row = _row(conn)
        assert int(row["is_active"]) == 1
        assert int(row["miss_count"]) == 0
    finally:
        conn.close()


def test_source_frozen_at_first_insert(monkeypatch, tmp_path):
    url = "https://same.example/jobs/1"
    ats_job = _intern_job(url=url, ext="ats1", source="example")
    portal_job = _intern_job(url=url, ext="portal-id", source="later-portal")
    monkeypatch.setattr("jobdigest.collect.load_companies", _enabled_companies)
    monkeypatch.setattr(
        "jobdigest.collect.load_portals",
        lambda: {
            "portals": [
                {
                    "name": "later-portal",
                    "url": "https://later.example/board",
                    "enabled": True,
                    "parser": "generic_cards",
                }
            ]
        },
    )
    monkeypatch.setattr("jobdigest.collect.load_feeds", _empty_feeds)
    db = tmp_path / "jobs.db"

    def fetch_fn(company, client, sleep):
        _ = company, client, sleep
        return FetchResult(FetchStatus.OK, 200, [ats_job])

    def portal_fn(row):
        _ = row
        return PortalResult(
            status="ok", jobs=[portal_job], http_status=200, name="later-portal"
        )

    assert (
        run(
            db_path=db,
            sleep=lambda _: None,
            fetch_fn=fetch_fn,
            portal_run_fn=portal_fn,
        )
        == 0
    )
    conn = connect(db)
    try:
        rows = conn.execute("SELECT source FROM jobs").fetchall()
        assert [r["source"] for r in rows] == ["example"]
    finally:
        conn.close()


def test_note_seen_and_bump_unit(tmp_path):
    db = tmp_path / "unit.db"
    conn = connect(db)
    profile = load_profile()
    try:
        action, jid = upsert_job(
            conn,
            profile,
            apply_url="https://u.example/j/1",
            hosted_url="https://u.example/j/1",
            source="unit",
            external_id="u1",
            company_id=None,
            company="Unit",
            title="Software Engineer Intern",
            location_raw="Pune",
            description_text="Intern role in Pune",
            raw_json="{}",
            ats="greenhouse",
            employment_hint="intern",
        )
        assert action == "insert" and jid is not None
        first = bump_unseen_for_source(conn, "unit", set(), success=True)
        assert first["missed"] == 1
        assert first["tombstoned"] == 0
        second = bump_unseen_for_source(conn, "unit", set(), success=True)
        assert second["tombstoned"] == 1
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        assert int(row["is_active"]) == 0
        note_seen_job(conn, jid)
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        assert int(row["is_active"]) == 1
        assert int(row["miss_count"]) == 0
        skipped = bump_unseen_for_source(conn, "unit", set(), success=False)
        assert skipped == {"missed": 0, "tombstoned": 0}
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        assert int(row["is_active"]) == 1
    finally:
        conn.close()
