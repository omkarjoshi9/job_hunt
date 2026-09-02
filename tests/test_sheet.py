from pathlib import Path

from jobdigest.cli import build_parser, main
from jobdigest.collect import run as collect_run
from jobdigest.config import load_models, load_profile
from jobdigest.db import connect, guest_rows, upsert_application, upsert_job, upsert_score
from jobdigest.sheet import HEADERS, run


class FakeSheet:
    def __init__(self, rows=None):
        self.rows = [list(r) for r in (rows or [])]
        self.appends = 0

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
            row = list(payload[0])
        else:
            row = list(payload)
        if not self.rows:
            self.rows.append(row)
        else:
            self.rows[0] = row


def _data_rows(sheet: FakeSheet) -> list[list[str]]:
    if not sheet.rows:
        return []
    first = [str(c).strip() for c in sheet.rows[0][:7]]
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
