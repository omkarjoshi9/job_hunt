from jobdigest.cli import build_parser, main
from jobdigest.sheet import HEADERS


class _FakeSheet:
    def __init__(self):
        self.rows = []
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


def test_help_lists_all_commands():
    parser = build_parser()
    text = parser.format_help()
    for name in ("collect", "score", "digest", "sheet-append"):
        assert name in text


def test_collect_no_longer_stub(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("JOBDIGEST_ENABLE", "")
    db = tmp_path / "jobs.db"
    code = main(["collect", "--db", str(db)])
    assert code == 0
    out = capsys.readouterr().out
    assert "not built" not in out
    assert "collect" in out
    assert db.is_file()


def test_sheet_append_no_longer_stub(monkeypatch, capsys, tmp_path):
    fake = _FakeSheet()
    monkeypatch.setattr("jobdigest.sheet.open_worksheet", lambda: fake)
    db = tmp_path / "jobs.db"
    code = main(["sheet-append", "--db", str(db), "--url", "https://example.com/job"])
    captured = capsys.readouterr()
    assert code == 0
    assert "not built" not in captured.out
    assert "sheet-append" in captured.out
    assert fake.appends == 1
    assert fake.rows[0] == HEADERS


def test_digest_empty_db_collect_failed(capsys, tmp_path):
    db = tmp_path / "jobs.db"
    code = main(["digest", "--db", str(db)])
    captured = capsys.readouterr()
    assert code == 1
    assert "COLLECT FAILED" in captured.out
    assert "COLLECT FAILED" in captured.err
    assert "not built" not in captured.out
    assert "not built" not in captured.err
