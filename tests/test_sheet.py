from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlite3

from jobdigest.cli import build_parser, main
from jobdigest.collect import run as collect_run
from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    connect,
    ensure_application,
    guest_rows,
    relevant_unapplied_jobs,
    upsert_application,
    upsert_job,
    upsert_score,
)
from jobdigest.sheet import (
    APPLIED_CHOICES,
    APPLIED_RANGE,
    CAMPUS_CHOICES,
    CAMPUS_OFF,
    CAMPUS_RANGE,
    HEADER_FORMAT,
    HEADER_RANGE,
    HEADERS,
    OLD_HEADERS,
    ensure_headers,
    run,
    sync,
)


class FakeSheet:
    def __init__(self, rows=None):
        self.rows = [list(r) for r in (rows or [])]
        self.appends = 0
        self.formats = []
        self.validations = []
        self.frozen_rows = None

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_row(self, values, value_input_option="USER_ENTERED"):
        _ = value_input_option
        self.rows.append(list(values))
        self.appends += 1

    def update(self, range_name, values=None, **kwargs):
        _ = kwargs
        payload = values if values is not None and not isinstance(range_name, list) else range_name
        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            if len(payload) > 1:
                self.rows = [list(r) for r in payload]
                return
            row = list(payload[0])
        else:
            row = list(payload)
        if not self.rows:
            self.rows.append(row)
        else:
            self.rows[0] = row

    def format(self, ranges, format):
        self.formats.append((ranges, dict(format)))

    def freeze(self, rows=None, cols=None):
        _ = cols
        self.frozen_rows = rows

    def add_validation(self, range, condition_type, values, **kwargs):
        self.validations.append((range, condition_type, list(values), dict(kwargs)))


def _data_rows(sheet: FakeSheet) -> list[list[str]]:
    if not sheet.rows:
        return []
    first = [str(c).strip() for c in sheet.rows[0][: len(HEADERS)]]
    if first == HEADERS:
        return sheet.rows[1:]
    return sheet.rows


def test_append_twice_one_row(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    sheet = FakeSheet()
    url = "https://dup.example/x"
    code1 = run(db_path=db, url=url, company="Acme", title="Intern", worksheet=sheet)
    code2 = run(db_path=db, url=url, company="Acme", title="Intern", worksheet=sheet)
    _ = capsys.readouterr()
    assert code1 == 0
    assert code2 == 0
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        assert count == 1
    finally:
        conn.close()
    assert len(_data_rows(sheet)) == 1
    assert sheet.appends == 1


def test_utm_pair_one_application_and_sheet_row(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    sheet = FakeSheet()
    code1 = run(
        db_path=db,
        url="https://x.com/a?utm_source=tg",
        company="X",
        title="Intern",
        worksheet=sheet,
    )
    code2 = run(db_path=db, url="https://x.com/a", company="X", title="Intern", worksheet=sheet)
    _ = capsys.readouterr()
    assert code1 == 0 and code2 == 0
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        assert count == 1
    finally:
        conn.close()
    assert len(_data_rows(sheet)) == 1
    assert sheet.appends == 1


def test_reconnect_existing_link_does_not_append(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    url = "https://dup.example/x"
    conn = connect(db)
    try:
        action, app_id = upsert_application(
            conn, apply_url=url, company="Acme", title="Intern"
        )
        assert action == "insert" and app_id is not None
    finally:
        conn.close()
    sheet = FakeSheet(
        rows=[
            HEADERS,
            ["2026-01-01", "Acme", "Intern", url, "greenhouse", "applied", ""],
        ]
    )
    code = run(db_path=db, worksheet=sheet)
    _ = capsys.readouterr()
    assert code == 0
    assert sheet.appends == 0
    conn = connect(db)
    try:
        row = conn.execute("SELECT sheet_row FROM applications").fetchone()
        assert row["sheet_row"] == 2
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        assert count == 1
    finally:
        conn.close()
    assert len(_data_rows(sheet)) == 1


def test_upsert_application_excludes_guest_rows(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    models = load_models()
    try:
        action, jid = upsert_job(
            conn,
            profile,
            apply_url="https://example.com/jobs/1",
            hosted_url="https://example.com/jobs/1",
            source="greenhouse",
            external_id="1",
            company_id=None,
            company="Acme",
            title="Software Engineer Intern",
            location_raw="Pune",
            description_text="Intern role in Pune",
            raw_json="{}",
            ats="greenhouse",
            employment_hint="intern",
        )
        assert action == "insert" and jid is not None
        upsert_score(
            conn,
            jid,
            models.score_model,
            models.score_prompt_version,
            True,
            90.0,
            {"why": "fit"},
        )
        before = guest_rows(
            conn, models.score_model, models.score_prompt_version, 12, profile=profile
        )
        assert len(before) == 1
        upsert_application(conn, apply_url="https://example.com/jobs/1")
        after = guest_rows(
            conn, models.score_model, models.score_prompt_version, 12, profile=profile
        )
        assert after == []
    finally:
        conn.close()


def test_guest_rows_skips_reject_even_if_relevant(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    models = load_models()
    try:
        action, jid = upsert_job(
            conn,
            profile,
            apply_url="https://example.com/jobs/reject-yoe",
            hosted_url="https://example.com/jobs/reject-yoe",
            source="greenhouse",
            external_id="reject-yoe",
            company_id=None,
            company="Notion",
            title="Software Engineer Intern",
            location_raw="Hyderabad, India",
            description_text="Intern role",
            raw_json="{}",
            ats="greenhouse",
            employment_hint="intern",
        )
        assert action == "insert" and jid is not None
        upsert_score(
            conn,
            jid,
            models.score_model,
            models.score_prompt_version,
            True,
            0.0,
            {"why": "stale"},
        )
        conn.execute(
            "UPDATE jobs SET hard_status = 'reject', hard_reason = 'yoe' WHERE id = ?",
            (jid,),
        )
        conn.commit()
        assert guest_rows(
            conn, models.score_model, models.score_prompt_version, 12, profile=profile
        ) == []
        assert relevant_unapplied_jobs(
            conn, models.score_model, models.score_prompt_version, profile=profile
        ) == []
    finally:
        conn.close()


def test_guest_rows_keeps_pass_relevant_score_zero(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    models = load_models()
    try:
        action, jid = upsert_job(
            conn,
            profile,
            apply_url="https://example.com/jobs/zero-score",
            hosted_url="https://example.com/jobs/zero-score",
            source="greenhouse",
            external_id="zero-score",
            company_id=None,
            company="Stripe",
            title="Software Engineer Intern",
            location_raw="Bengaluru",
            description_text="Intern role",
            raw_json="{}",
            ats="greenhouse",
            employment_hint="intern",
        )
        assert action == "insert" and jid is not None
        upsert_score(
            conn,
            jid,
            models.score_model,
            models.score_prompt_version,
            True,
            0.0,
            {"why": "fit"},
        )
        from dataclasses import replace

        guests = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=replace(profile, digest_min_quality=0.0),
        )
        assert [int(g["id"]) for g in guests] == [jid]
    finally:
        conn.close()


def test_guest_rows_skips_inactive(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    profile = load_profile()
    models = load_models()
    try:
        action, jid = upsert_job(
            conn,
            profile,
            apply_url="https://example.com/jobs/inactive",
            hosted_url="https://example.com/jobs/inactive",
            source="greenhouse",
            external_id="inactive",
            company_id=None,
            company="Stripe",
            title="Software Engineer Intern",
            location_raw="Bengaluru",
            description_text="Intern role",
            raw_json="{}",
            ats="greenhouse",
            employment_hint="intern",
        )
        assert action == "insert" and jid is not None
        upsert_score(
            conn,
            jid,
            models.score_model,
            models.score_prompt_version,
            True,
            1.0,
            {"why": "fit"},
        )
        conn.execute("UPDATE jobs SET is_active = 0 WHERE id = ?", (jid,))
        conn.commit()
        assert guest_rows(
            conn, models.score_model, models.score_prompt_version, 12, profile=profile
        ) == []
    finally:
        conn.close()


def test_missing_creds_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("jobdigest.sheet.load_dotenv", lambda path=None: None)
    monkeypatch.setenv("JOBDIGEST_REQUIRE_SHEET", "1")
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    db = tmp_path / "jobs.db"
    code = run(db_path=db, url="https://example.com/job")
    captured = capsys.readouterr()
    assert code == 1
    assert "not built" not in captured.out
    assert "not built" not in captured.err
    assert "SHEET FAILED missing GOOGLE_SHEET_ID or GOOGLE_SERVICE_ACCOUNT_JSON" in captured.err
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        assert count == 1
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM applications WHERE sheet_row IS NULL"
        ).fetchone()["n"]
        assert pending == 1
    finally:
        conn.close()


def test_sheet_append_missing_creds_skips_when_not_required(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("jobdigest.sheet.load_dotenv", lambda path=None: None)
    monkeypatch.delenv("JOBDIGEST_REQUIRE_SHEET", raising=False)
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    db = tmp_path / "jobs.db"
    code = run(db_path=db, url="https://example.com/job")
    captured = capsys.readouterr()
    assert code == 0
    assert "sheet-append skip missing credentials" in captured.out
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        assert count == 1
    finally:
        conn.close()


def test_collect_disconnected_from_sheets(monkeypatch, tmp_path):
    monkeypatch.setattr("jobdigest.collect.load_companies", lambda: {"companies": []})
    db = tmp_path / "jobs.db"
    code = collect_run(db_path=db, sleep=lambda _: None)
    assert code == 0


ROOT = Path(__file__).resolve().parents[1]


def test_collect_score_digest_source_has_no_gspread():
    for name in ("collect.py", "score.py", "digest.py"):
        text = (ROOT / "src" / "jobdigest" / name).read_text(encoding="utf-8").lower()
        assert "gspread" not in text, name


def _relevant_job(
    conn,
    *,
    url: str,
    company: str,
    title: str,
    location_raw: str = "Pune",
    score: float = 90.0,
    first_seen_at: str | None = None,
):
    profile = load_profile()
    models = load_models()
    action, jid = upsert_job(
        conn,
        profile,
        apply_url=url,
        hosted_url=url,
        source="greenhouse",
        external_id=url.rsplit("/", 1)[-1],
        company_id=None,
        company=company,
        title=title,
        location_raw=location_raw,
        description_text="Intern role in Pune",
        raw_json="{}",
        ats="greenhouse",
        employment_hint="intern",
    )
    assert action == "insert" and jid is not None
    upsert_score(
        conn,
        jid,
        models.score_model,
        models.score_prompt_version,
        True,
        score,
        {"why": "fit"},
    )
    if first_seen_at is not None:
        conn.execute(
            "UPDATE jobs SET first_seen_at = ? WHERE id = ?", (first_seen_at, jid)
        )
        conn.commit()
    return jid


RANK_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _rank_iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rank_profile():
    return replace(load_profile(), location="Pune", relocate=["Bengaluru"])


def _guest_ids(conn, *, digest_max=12, now=RANK_NOW):
    models = load_models()
    rows = guest_rows(
        conn,
        models.score_model,
        models.score_prompt_version,
        digest_max,
        profile=_rank_profile(),
        now=now,
    )
    return [int(r["id"]) for r in rows]


def test_sheet_sync_appends_offcampus_blank_applied(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        _relevant_job(
            conn,
            url="https://new.example/intern",
            company="Acme",
            title="Software Engineer Intern",
        )
    finally:
        conn.close()
    sheet = FakeSheet()
    assert sync(db_path=db, worksheet=sheet) == 0
    _ = capsys.readouterr()
    rows = _data_rows(sheet)
    assert len(rows) == 1
    assert rows[0][1] == "Acme"
    assert rows[0][3] == "https://new.example/intern"
    assert rows[0][5] == CAMPUS_OFF
    assert rows[0][6] == ""
    assert rows[0][7] == ""
    models = load_models()
    conn = connect(db)
    try:
        apps = conn.execute("SELECT status, notes FROM applications").fetchall()
        assert len(apps) == 1
        assert apps[0]["status"] == "saved"
        assert guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=load_profile(),
        ) == []
    finally:
        conn.close()
    snapshot = [list(r) for r in sheet.rows]
    assert sync(db_path=db, worksheet=sheet) == 0
    _ = capsys.readouterr()
    assert sheet.rows == snapshot
    assert sheet.appends == 1
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        assert count == 1
    finally:
        conn.close()


def test_sheet_sync_keeps_hand_edited_applied_status(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    url = "https://edit.example/intern"
    conn = connect(db)
    try:
        _relevant_job(
            conn,
            url=url,
            company="Acme",
            title="Software Engineer Intern",
        )
    finally:
        conn.close()
    sheet = FakeSheet(
        rows=[
            HEADERS,
            [
                "2026-01-01",
                "Acme",
                "Software Engineer Intern",
                url,
                "greenhouse",
                CAMPUS_OFF,
                "yes",
                "oa",
                "mine",
            ],
        ]
    )
    before = [list(r) for r in sheet.rows]
    assert sync(db_path=db, worksheet=sheet) == 0
    _ = capsys.readouterr()
    assert sheet.appends == 0
    assert sheet.rows == before
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT status, notes FROM applications WHERE apply_url_norm = ?",
            (url,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "saved"
        assert row["notes"] in ("", None)
        models = load_models()
        assert guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=load_profile(),
        ) == []
    finally:
        conn.close()


def test_sheet_sync_skips_oncampus_url(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    url = "https://campus.example/intern"
    conn = connect(db)
    try:
        _relevant_job(
            conn,
            url=url,
            company="CollegeCo",
            title="Software Engineer Intern",
        )
    finally:
        conn.close()
    sheet = FakeSheet(
        rows=[
            HEADERS,
            [
                "2026-01-01",
                "CollegeCo",
                "Software Engineer Intern",
                url,
                "",
                "oncampus",
                "",
                "",
                "",
            ],
        ]
    )
    before = [list(r) for r in sheet.rows]
    assert sync(db_path=db, worksheet=sheet) == 0
    _ = capsys.readouterr()
    assert sheet.appends == 0
    assert sheet.rows[1][5] == "oncampus"
    assert sheet.rows == before
    conn = connect(db)
    try:
        row = conn.execute("SELECT status FROM applications").fetchone()
        assert row is not None
        assert row["status"] == "saved"
        models = load_models()
        assert guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=load_profile(),
        ) == []
    finally:
        conn.close()


def test_sheet_sync_migrates_old_headers(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        _relevant_job(
            conn,
            url="https://old.example/intern",
            company="Acme",
            title="Software Engineer Intern",
        )
    finally:
        conn.close()
    sheet = FakeSheet(
        rows=[
            OLD_HEADERS,
            [
                "2026-01-01",
                "KeepCo",
                "Intern",
                "https://keep.example/a",
                "greenhouse",
                "oa",
                "note",
            ],
        ]
    )
    assert sync(db_path=db, worksheet=sheet) == 0
    _ = capsys.readouterr()
    assert sheet.rows[0] == HEADERS
    keep = sheet.rows[1]
    assert keep[1] == "KeepCo"
    assert keep[5] == CAMPUS_OFF
    assert keep[6] == ""
    assert keep[7] == "oa"
    assert keep[8] == "note"
    assert any(row[3] == "https://old.example/intern" for row in sheet.rows[1:])


def _assert_layout(sheet: FakeSheet) -> None:
    assert sheet.frozen_rows == 1
    assert sheet.formats == [(HEADER_RANGE, HEADER_FORMAT)]
    campus, applied = sheet.validations
    assert campus[0] == CAMPUS_RANGE
    assert campus[2] == list(CAMPUS_CHOICES)
    assert campus[3]["showCustomUi"] is True
    assert campus[3]["strict"] is False
    assert applied[0] == APPLIED_RANGE
    assert applied[2] == list(APPLIED_CHOICES)
    assert applied[3]["showCustomUi"] is True
    assert applied[3]["strict"] is False


def test_ensure_headers_styles_and_dropdowns_without_rewriting_rows():
    existing = [
        HEADERS,
        [
            "2026-09-02",
            "Stripe",
            "Software Engineer Intern",
            "https://stripe.com/jobs/1",
            "stripe",
            CAMPUS_OFF,
            "",
            "",
            "",
        ],
    ]
    sheet = FakeSheet(rows=existing)
    ensure_headers(sheet)
    assert sheet.rows == existing
    _assert_layout(sheet)


def test_sheet_sync_applies_layout_when_headers_already_match(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    url = "https://layout.example/intern"
    sheet = FakeSheet(
        rows=[
            HEADERS,
            [
                "2026-01-01",
                "Acme",
                "Intern",
                url,
                "greenhouse",
                CAMPUS_OFF,
                "",
                "",
                "",
            ],
        ]
    )
    assert sync(db_path=db, worksheet=sheet) == 0
    _ = capsys.readouterr()
    assert sheet.appends == 0
    _assert_layout(sheet)


def test_migrate_applications_check_allows_saved(tmp_path):
    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE applications (
          id INTEGER PRIMARY KEY,
          job_id INTEGER,
          apply_url_norm TEXT NOT NULL UNIQUE,
          company TEXT,
          title TEXT,
          applied_on TEXT NOT NULL,
          status TEXT NOT NULL CHECK (status IN ('applied','oa','interview','offer','rejected','ghosted')),
          source TEXT,
          notes TEXT,
          sheet_row INTEGER,
          synced_at TEXT
        );
        INSERT INTO applications (
          apply_url_norm, company, title, applied_on, status
        ) VALUES (
          'https://old.example/a', 'Acme', 'Intern', '2026-01-01T00:00:00Z', 'applied'
        );
        """
    )
    raw.close()
    conn = connect(db)
    try:
        action, app_id = upsert_application(
            conn, apply_url="https://old.example/saved", status="saved"
        )
        assert action == "insert" and app_id is not None
        row = conn.execute(
            "SELECT status FROM applications WHERE apply_url_norm = ?",
            ("https://old.example/a",),
        ).fetchone()
        assert row["status"] == "applied"
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'"
        ).fetchone()["sql"]
        assert "'saved'" in sql
        assert "'hidden'" in sql
    finally:
        conn.close()


def test_upsert_application_saved_and_rejects_unknown(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        action, app_id = upsert_application(
            conn, apply_url="https://s.example/a", status="saved"
        )
        assert action == "insert" and app_id is not None
        row = conn.execute(
            "SELECT status FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        assert row["status"] == "saved"
        hidden_action, hidden_id = upsert_application(
            conn, apply_url="https://s.example/h", status="hidden"
        )
        assert hidden_action == "insert" and hidden_id is not None
        skip, none = upsert_application(
            conn, apply_url="https://s.example/b", status="nope"
        )
        assert skip == "skip" and none is None
    finally:
        conn.close()


def test_upsert_application_restamps_applied_on_when_entering_applied(
    monkeypatch, tmp_path
):
    conn = connect(tmp_path / "jobs.db")
    try:
        action, app_id = upsert_application(
            conn, apply_url="https://s.example/stamp", status="saved"
        )
        assert action == "insert" and app_id is not None
        first = conn.execute(
            "SELECT applied_on FROM applications WHERE id = ?", (app_id,)
        ).fetchone()["applied_on"]
        monkeypatch.setattr("jobdigest.db.utc_now", lambda: "2099-01-01T00:00:00Z")
        again, same = upsert_application(
            conn, apply_url="https://s.example/stamp", status="applied"
        )
        assert again == "update" and same == app_id
        row = conn.execute(
            "SELECT applied_on, status FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        assert row["status"] == "applied"
        assert row["applied_on"] == "2099-01-01T00:00:00Z"
        assert row["applied_on"] != first
        monkeypatch.setattr("jobdigest.db.utc_now", lambda: "2099-02-01T00:00:00Z")
        upsert_application(
            conn, apply_url="https://s.example/stamp", status="applied"
        )
        again_on = conn.execute(
            "SELECT applied_on FROM applications WHERE id = ?", (app_id,)
        ).fetchone()["applied_on"]
        assert again_on == "2099-01-01T00:00:00Z"
    finally:
        conn.close()


def test_ensure_application_does_not_overwrite_status(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        action, app_id = upsert_application(
            conn, apply_url="https://e.example/oa", status="oa", notes="mine"
        )
        assert action == "insert" and app_id is not None
        again, same_id = ensure_application(
            conn, apply_url="https://e.example/oa", status="saved", notes="overwrite?"
        )
        assert again == "exists" and same_id == app_id
        row = conn.execute(
            "SELECT status, notes FROM applications WHERE id = ?", (app_id,)
        ).fetchone()
        assert row["status"] == "oa"
        assert row["notes"] == "mine"
        inserted, new_id = ensure_application(
            conn, apply_url="https://e.example/new", status="saved"
        )
        assert inserted == "insert" and new_id is not None
        saved = conn.execute(
            "SELECT status FROM applications WHERE id = ?", (new_id,)
        ).fetchone()
        assert saved["status"] == "saved"
    finally:
        conn.close()


def test_sheet_sync_leaves_existing_oa_application(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    url = "https://oa.example/intern"
    conn = connect(db)
    try:
        _relevant_job(
            conn,
            url=url,
            company="Acme",
            title="Software Engineer Intern",
        )
        upsert_application(conn, apply_url=url, status="oa", notes="mine")
    finally:
        conn.close()
    sheet = FakeSheet(
        rows=[
            HEADERS,
            [
                "2026-01-01",
                "Acme",
                "Software Engineer Intern",
                url,
                "greenhouse",
                CAMPUS_OFF,
                "yes",
                "oa",
                "mine",
            ],
        ]
    )
    before = [list(r) for r in sheet.rows]
    assert sync(db_path=db, worksheet=sheet) == 0
    _ = capsys.readouterr()
    assert sheet.appends == 0
    assert sheet.rows == before
    conn = connect(db)
    try:
        row = conn.execute("SELECT status, notes FROM applications").fetchone()
        assert row["status"] == "oa"
        assert row["notes"] == "mine"
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        assert count == 1
    finally:
        conn.close()


def test_sheet_sync_require_sheet_missing_creds_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("jobdigest.sheet.load_dotenv", lambda path=None: None)
    monkeypatch.setenv("JOBDIGEST_REQUIRE_SHEET", "1")
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        _relevant_job(
            conn,
            url="https://miss-req.example/intern",
            company="Acme",
            title="Software Engineer Intern",
        )
    finally:
        conn.close()
    code = sync(db_path=db)
    captured = capsys.readouterr()
    assert code == 1
    assert "SHEET FAILED missing GOOGLE_SHEET_ID or GOOGLE_SERVICE_ACCOUNT_JSON" in captured.err
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        assert count == 0
    finally:
        conn.close()


def test_sheet_sync_missing_creds_skips_when_not_required(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("jobdigest.sheet.load_dotenv", lambda path=None: None)
    monkeypatch.delenv("JOBDIGEST_REQUIRE_SHEET", raising=False)
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        _relevant_job(
            conn,
            url="https://miss.example/intern",
            company="Acme",
            title="Software Engineer Intern",
        )
    finally:
        conn.close()
    code = sync(db_path=db)
    captured = capsys.readouterr()
    assert code == 0
    assert "sheet-sync skip missing credentials" in captured.out
    conn = connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) AS n FROM applications").fetchone()["n"]
        assert count == 0
        models = load_models()
        assert guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=load_profile(),
        )
    finally:
        conn.close()


def test_guest_rows_equal_score_sorts_by_recency(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        older = _relevant_job(
            conn,
            url="https://rank.example/old",
            company="OldCo",
            title="Software Engineer Intern",
            location_raw="Pune",
            score=80.0,
            first_seen_at=_rank_iso(RANK_NOW - timedelta(days=6)),
        )
        newer = _relevant_job(
            conn,
            url="https://rank.example/new",
            company="NewCo",
            title="Software Engineer Intern",
            location_raw="Pune",
            score=80.0,
            first_seen_at=_rank_iso(RANK_NOW - timedelta(hours=4)),
        )
        assert _guest_ids(conn)[:2] == [newer, older]
    finally:
        conn.close()


def test_guest_rows_pune_outranks_equal_score_equal_age_bengaluru(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        seen = _rank_iso(RANK_NOW)
        pune = _relevant_job(
            conn,
            url="https://rank.example/pune",
            company="PuneCo",
            title="Software Engineer Intern",
            location_raw="Pune",
            score=80.0,
            first_seen_at=seen,
        )
        blr = _relevant_job(
            conn,
            url="https://rank.example/blr",
            company="BlrCo",
            title="Software Engineer Intern",
            location_raw="Bengaluru",
            score=80.0,
            first_seen_at=seen,
        )
        assert _guest_ids(conn)[:2] == [pune, blr]
    finally:
        conn.close()


def test_guest_rows_bangalore_counts_as_bengaluru(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        seen = _rank_iso(RANK_NOW)
        bangalore = _relevant_job(
            conn,
            url="https://rank.example/bangalore",
            company="BlrCo",
            title="Software Engineer Intern",
            location_raw="Bangalore",
            score=80.0,
            first_seen_at=seen,
        )
        mumbai = _relevant_job(
            conn,
            url="https://rank.example/mumbai",
            company="MumCo",
            title="Software Engineer Intern",
            location_raw="Mumbai",
            score=80.0,
            first_seen_at=seen,
        )
        assert _guest_ids(conn)[:2] == [bangalore, mumbai]
    finally:
        conn.close()


def test_guest_rows_example_a_fresh_pune_beats_stale_higher_score(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        pune = _relevant_job(
            conn,
            url="https://rank.example/fresh-pune",
            company="PuneCo",
            title="Software Engineer Intern",
            location_raw="Pune",
            score=78.0,
            first_seen_at=_rank_iso(RANK_NOW - timedelta(hours=4)),
        )
        mumbai = _relevant_job(
            conn,
            url="https://rank.example/stale-mumbai",
            company="MumCo",
            title="Software Engineer Intern",
            location_raw="Mumbai",
            score=81.0,
            first_seen_at=_rank_iso(RANK_NOW - timedelta(days=6)),
        )
        assert _guest_ids(conn)[:2] == [pune, mumbai]
    finally:
        conn.close()


def test_guest_rows_unparseable_first_seen_does_not_crash(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        bogus = _relevant_job(
            conn,
            url="https://rank.example/bogus",
            company="BogusCo",
            title="Software Engineer Intern",
            location_raw="Pune",
            score=90.0,
            first_seen_at="bogus",
        )
        fresh = _relevant_job(
            conn,
            url="https://rank.example/fresh",
            company="FreshCo",
            title="Software Engineer Intern",
            location_raw="Pune",
            score=90.0,
            first_seen_at=_rank_iso(RANK_NOW),
        )
        ids = _guest_ids(conn)
        assert ids[0] == fresh
        assert bogus in ids
    finally:
        conn.close()


def test_guest_rows_digest_max_applies_after_blend(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    try:
        pune = _relevant_job(
            conn,
            url="https://rank.example/cap-pune",
            company="PuneCo",
            title="Software Engineer Intern",
            location_raw="Pune",
            score=78.0,
            first_seen_at=_rank_iso(RANK_NOW - timedelta(hours=4)),
        )
        mumbai = _relevant_job(
            conn,
            url="https://rank.example/cap-mumbai",
            company="MumCo",
            title="Software Engineer Intern",
            location_raw="Mumbai",
            score=81.0,
            first_seen_at=_rank_iso(RANK_NOW - timedelta(days=6)),
        )
        hyd = _relevant_job(
            conn,
            url="https://rank.example/cap-hyd",
            company="HydCo",
            title="Software Engineer Intern",
            location_raw="Hyderabad",
            score=80.0,
            first_seen_at=_rank_iso(RANK_NOW - timedelta(days=10)),
        )
        ids = _guest_ids(conn, digest_max=2)
        assert ids == [pune, mumbai]
        assert hyd not in ids
    finally:
        conn.close()


def test_relevant_unapplied_jobs_matches_guest_order_without_limit(tmp_path):
    db = tmp_path / "jobs.db"
    conn = connect(db)
    models = load_models()
    profile = _rank_profile()
    try:
        for i in range(13):
            _relevant_job(
                conn,
                url=f"https://rank.example/many-{i:02d}",
                company=f"Co{i:02d}",
                title="Software Engineer Intern",
                location_raw="Pune" if i % 2 == 0 else "Mumbai",
                score=70.0 + i,
                first_seen_at=_rank_iso(RANK_NOW - timedelta(hours=i)),
            )
        guests = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            999,
            profile=profile,
            now=RANK_NOW,
        )
        all_jobs = relevant_unapplied_jobs(
            conn,
            models.score_model,
            models.score_prompt_version,
            profile=profile,
            now=RANK_NOW,
        )
        assert len(all_jobs) == 13
        assert [int(r["id"]) for r in all_jobs] == [int(r["id"]) for r in guests]
        capped = guest_rows(
            conn,
            models.score_model,
            models.score_prompt_version,
            12,
            profile=profile,
            now=RANK_NOW,
        )
        assert len(capped) == 12
    finally:
        conn.close()


def test_sheet_sync_appends_in_blend_order(tmp_path, capsys):
    db = tmp_path / "jobs.db"
    wall = datetime.now(timezone.utc)
    conn = connect(db)
    try:
        _relevant_job(
            conn,
            url="https://rank.example/sync-mumbai",
            company="MumCo",
            title="Software Engineer Intern",
            location_raw="Mumbai",
            score=81.0,
            first_seen_at=_rank_iso(wall - timedelta(days=6)),
        )
        _relevant_job(
            conn,
            url="https://rank.example/sync-pune",
            company="PuneCo",
            title="Software Engineer Intern",
            location_raw="Pune",
            score=78.0,
            first_seen_at=_rank_iso(wall - timedelta(hours=4)),
        )
    finally:
        conn.close()
    sheet = FakeSheet()
    assert sync(db_path=db, worksheet=sheet) == 0
    _ = capsys.readouterr()
    rows = _data_rows(sheet)
    assert len(rows) == 2
    assert rows[0][1] == "PuneCo"
    assert rows[0][3] == "https://rank.example/sync-pune"
    assert rows[1][1] == "MumCo"

