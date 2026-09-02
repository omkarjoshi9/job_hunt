from pathlib import Path

from jobdigest.cli import build_parser, main
from jobdigest.collect import run as collect_run
from jobdigest.config import load_models, load_profile
from jobdigest.db import connect, guest_rows, upsert_application, upsert_job, upsert_score
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
            conn, models.score_model, models.score_prompt_version, 12
        )
        assert len(before) == 1
        upsert_application(conn, apply_url="https://example.com/jobs/1")
        after = guest_rows(
            conn, models.score_model, models.score_prompt_version, 12
        )
        assert after == []
    finally:
        conn.close()


def test_missing_creds_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("jobdigest.sheet.load_dotenv", lambda path=None: None)
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    db = tmp_path / "jobs.db"
    code = run(db_path=db, url="https://example.com/job")
    captured = capsys.readouterr()
    assert code == 1
    assert "not built" not in captured.out
    assert "not built" not in captured.err
    assert "sheet-append error" in captured.out or "sheet-append error" in captured.err
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


def test_collect_disconnected_from_sheets(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBDIGEST_ENABLE", "")
    db = tmp_path / "jobs.db"
    code = collect_run(db_path=db, sleep=lambda _: None)
    assert code == 0


ROOT = Path(__file__).resolve().parents[1]


def test_collect_score_digest_source_has_no_gspread():
    for name in ("collect.py", "score.py", "digest.py"):
        text = (ROOT / "src" / "jobdigest" / name).read_text(encoding="utf-8").lower()
        assert "gspread" not in text, name


def _relevant_job(conn, *, url: str, company: str, title: str):
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
    return jid


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
    snapshot = [list(r) for r in sheet.rows]
    assert sync(db_path=db, worksheet=sheet) == 0
    _ = capsys.readouterr()
    assert sheet.rows == snapshot
    assert sheet.appends == 1


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
