from datetime import datetime, timedelta, timezone

from jobdigest.db import connect, upsert_application
from jobdigest.followups import (
    IST,
    due_followups,
    format_followups,
    ist_business_days_age,
)


def _utc(year: int, month: int, day: int, hour: int = 6, minute: int = 30) -> datetime:
    """Noon IST on that calendar date (06:30 UTC)."""
    _ = hour, minute
    return datetime(year, month, day, 6, 30, tzinfo=timezone.utc)


def test_ist_business_days_monday_to_next_monday_is_five():
    applied = "2026-08-31T00:30:00Z"
    now = _utc(2026, 9, 7)
    assert IST.utcoffset(None) == timedelta(hours=5, minutes=30)
    assert ist_business_days_age(applied, now) == 5


def test_ist_business_days_monday_to_wednesday_week_plus_is_seven():
    applied = "2026-08-31T00:30:00Z"
    now = _utc(2026, 9, 9)
    assert ist_business_days_age(applied, now) == 7


def test_ist_business_days_friday_to_following_tuesday_is_seven():
    applied = "2026-08-28T00:30:00Z"
    now = _utc(2026, 9, 8)
    assert ist_business_days_age(applied, now) == 7


def test_due_followups_six_days_absent_seven_present(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        upsert_application(
            conn,
            apply_url="https://example.com/jobs/f1",
            company="Acme",
            title="Intern",
            status="applied",
        )
        conn.execute(
            "UPDATE applications SET applied_on = ? WHERE apply_url_norm = ?",
            ("2026-08-31T00:30:00Z", "https://example.com/jobs/f1"),
        )
        conn.commit()
        six = due_followups(conn, now=_utc(2026, 9, 7))
        seven = due_followups(conn, now=_utc(2026, 9, 9))
    finally:
        conn.close()
    assert six == []
    assert len(seven) == 1
    assert seven[0]["company"] == "Acme"


def test_due_followups_excludes_saved_hidden_oa(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        for i, status in enumerate(("saved", "hidden", "oa", "applied")):
            upsert_application(
                conn,
                apply_url=f"https://example.com/jobs/{status}",
                company=status,
                title="Intern",
                status=status,
            )
            conn.execute(
                "UPDATE applications SET applied_on = ? WHERE apply_url_norm = ?",
                ("2026-08-01T00:00:00Z", f"https://example.com/jobs/{status}"),
            )
        conn.commit()
        rows = due_followups(conn, now=_utc(2026, 9, 9))
    finally:
        conn.close()
    assert [r["company"] for r in rows] == ["applied"]


def test_due_followups_caps_at_five_oldest(tmp_path):
    conn = connect(tmp_path / "jobs.db")
    try:
        for i in range(6):
            day = 10 + i
            upsert_application(
                conn,
                apply_url=f"https://example.com/jobs/c{i}",
                company=f"Co{i}",
                title="Intern",
                status="applied",
            )
            conn.execute(
                "UPDATE applications SET applied_on = ? WHERE apply_url_norm = ?",
                (f"2026-08-{day:02d}T00:00:00Z", f"https://example.com/jobs/c{i}"),
            )
        conn.commit()
        rows = due_followups(conn, now=_utc(2026, 9, 9))
    finally:
        conn.close()
    assert [r["company"] for r in rows] == [f"Co{i}" for i in range(5)]


def test_format_followups_empty():
    now = _utc(2026, 9, 9)
    assert format_followups([], now=now) == "No follow-ups due."
