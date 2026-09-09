from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
FOLLOWUP_LIMIT = 5
FOLLOWUP_BUSINESS_DAYS = 7

RowLike = Any


def parse_iso(value: str | None) -> datetime | None:
    try:
        text = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ist_date(value: datetime) -> date:
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).date()


def ist_business_days_age(applied_on: str | None, now: datetime) -> int:
    start_dt = parse_iso(applied_on)
    if start_dt is None:
        return 0
    start = ist_date(start_dt)
    end = ist_date(now)
    if end <= start:
        return 0
    n = 0
    cursor = start
    one = timedelta(days=1)
    while cursor < end:
        cursor += one
        if cursor.weekday() < 5:
            n += 1
    return n


def ist_calendar_days_age(applied_on: str | None, now: datetime) -> int:
    start_dt = parse_iso(applied_on)
    if start_dt is None:
        return 0
    delta = ist_date(now) - ist_date(start_dt)
    return max(0, delta.days)


def due_followups(
    conn: Any, *, now: datetime, limit: int = FOLLOWUP_LIMIT
) -> list[RowLike]:
    rows = conn.execute(
        """
        SELECT applications.*, jobs.apply_url AS job_apply_url
        FROM applications
        LEFT JOIN jobs ON jobs.id = applications.job_id
        WHERE applications.status = 'applied'
        ORDER BY applications.applied_on ASC, applications.id ASC
        """
    ).fetchall()
    cap = max(int(limit), 0)
    out: list[RowLike] = []
    for row in rows:
        if ist_business_days_age(row["applied_on"], now) < FOLLOWUP_BUSINESS_DAYS:
            continue
        out.append(row)
        if len(out) >= cap:
            break
    return out


def format_followups(rows: list[RowLike], *, now: datetime) -> str:
    if not rows:
        return "No follow-ups due."
    blocks: list[str] = []
    for row in rows:
        company = str(row["company"] or "").strip()
        title = str(row["title"] or "").strip()
        url = str(row["job_apply_url"] or "").strip()
        if not (url.lower().startswith("http://") or url.lower().startswith("https://")):
            url = str(row["apply_url_norm"] or "").strip()
        n = ist_calendar_days_age(row["applied_on"], now)
        lines = [f"{company} — {title}"]
        if url.lower().startswith("http://") or url.lower().startswith("https://"):
            lines.append(url)
        lines.append(f"applied {n}d")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
