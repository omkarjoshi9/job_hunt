from datetime import datetime, timezone
from pathlib import Path

from jobdigest.cron import DEFAULT_CRON, DEFAULT_URGENT_CRON, due_slot, is_due, main, scheduled_slot

ROOT = Path(__file__).resolve().parents[1]


def _utc(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 3, hour, minute, tzinfo=timezone.utc)


def test_default_cron_is_0930_ist_not_0400():
    assert DEFAULT_CRON == "30 3 * * *"
    assert DEFAULT_CRON != "0 4 * * *"
    assert DEFAULT_CRON != "30 4 * * *"


def test_schedule_due_at_0330():
    assert is_due(now=_utc(3, 30), event="schedule", cron="30 3 * * *") is True


def test_schedule_due_when_delayed_inside_slot():
    assert is_due(now=_utc(3, 41), event="schedule", cron="30 3 * * *") is True


def test_schedule_not_due_at_0300():
    assert is_due(now=_utc(3, 0), event="schedule", cron="30 3 * * *") is False


def test_schedule_not_due_next_slot():
    assert is_due(now=_utc(3, 45), event="schedule", cron="30 3 * * *") is False


def test_empty_cron_uses_default():
    assert is_due(now=_utc(3, 30), event="schedule", cron="") is True
    assert is_due(now=_utc(3, 0), event="schedule", cron="") is False
    assert is_due(now=_utc(4, 30), event="schedule", cron="") is False


def test_custom_cron_1030_ist_still_works():
    assert is_due(now=_utc(4, 30), event="schedule", cron="30 4 * * *") is True
    assert is_due(now=_utc(4, 41), event="schedule", cron="30 4 * * *") is True
    assert is_due(now=_utc(4, 0), event="schedule", cron="30 4 * * *") is False
    assert is_due(now=_utc(4, 45), event="schedule", cron="30 4 * * *") is False


def test_dispatch_always_due():
    assert is_due(now=_utc(12, 0), event="workflow_dispatch", cron="30 4 * * *") is True
    assert is_due(now=_utc(12, 0), event="", cron="30 3 * * *") is True


def test_custom_cron():
    assert is_due(now=_utc(5, 0), event="schedule", cron="0 5 * * *") is True
    assert is_due(now=_utc(4, 30), event="schedule", cron="0 5 * * *") is False


def test_scheduled_slot_floors_to_15():
    assert scheduled_slot(_utc(4, 41)).minute == 30
    assert scheduled_slot(_utc(4, 45)).minute == 45


def test_main_writes_github_output(tmp_path, monkeypatch, capsys):
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("JOBDIGEST_CRON", "30 3 * * *")
    assert main() == 0
    text = out.read_text(encoding="utf-8")
    assert "due=true" in text
    captured = capsys.readouterr()
    assert "due=true" in captured.out


def test_main_invalid_cron_exits_1(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("JOBDIGEST_CRON", "not-a-cron")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert main() == 1
    captured = capsys.readouterr()
    assert "due=" not in captured.out
    assert "cron" in captured.err.lower()


def test_cron_module_is_stdlib_only():
    text = (ROOT / "src" / "jobdigest" / "cron.py").read_text(encoding="utf-8").lower()
    for word in ("gspread", "httpx", "openrouter", "openai"):
        assert word not in text


def test_due_slot_dispatch_is_digest():
    assert due_slot(now=_utc(12, 0), event="workflow_dispatch", cron="30 3 * * *") == "digest"


def test_due_slot_weekday_0830_utc_is_urgent():
    assert DEFAULT_URGENT_CRON == "30 8 * * 1-5"
    now = datetime(2026, 9, 3, 8, 30, tzinfo=timezone.utc)
    assert due_slot(now=now, event="schedule", cron="30 3 * * *") == "urgent"


def test_due_slot_saturday_0830_utc_not_urgent():
    now = datetime(2026, 9, 5, 8, 30, tzinfo=timezone.utc)
    assert due_slot(now=now, event="schedule", cron="30 3 * * *") is None


def test_due_slot_sunday_0330_utc_is_digest():
    now = datetime(2026, 9, 6, 3, 30, tzinfo=timezone.utc)
    assert due_slot(now=now, event="schedule", cron="30 3 * * *") == "digest"


def test_due_slot_urgent_off():
    now = datetime(2026, 9, 3, 8, 30, tzinfo=timezone.utc)
    assert due_slot(
        now=now, event="schedule", cron="30 3 * * *", urgent_cron="off"
    ) is None


def test_due_slot_empty_urgent_uses_default():
    now = datetime(2026, 9, 3, 8, 30, tzinfo=timezone.utc)
    assert due_slot(now=now, event="schedule", cron="30 3 * * *", urgent_cron="") == "urgent"


def test_due_slot_both_match_prefers_digest():
    now = datetime(2026, 9, 3, 8, 30, tzinfo=timezone.utc)
    assert (
        due_slot(
            now=now,
            event="schedule",
            cron="30 8 * * 1-5",
            urgent_cron="30 8 * * 1-5",
        )
        == "digest"
    )


def test_main_writes_slot(tmp_path, monkeypatch, capsys):
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("JOBDIGEST_CRON", "30 3 * * *")
    assert main() == 0
    text = out.read_text(encoding="utf-8")
    assert "due=true" in text
    assert "slot=digest" in text
    captured = capsys.readouterr()
    assert "slot=digest" in captured.out
