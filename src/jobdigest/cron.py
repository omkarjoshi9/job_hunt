from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

DEFAULT_CRON = "30 4 * * *"
TICK_MINUTES = 15


def scheduled_slot(now: datetime) -> datetime:
    now = now.replace(second=0, microsecond=0)
    minute = (now.minute // TICK_MINUTES) * TICK_MINUTES
    return now.replace(minute=minute)


def _matches_field(field: str, value: int, minimum: int, maximum: int) -> bool:
    text = (field or "").strip()
    if not text:
        raise ValueError("empty cron field")
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError("empty cron field part")
        step = 1
        if "/" in part:
            range_part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) <= 0:
                raise ValueError(f"invalid cron step {part!r}")
            step = int(step_s)
            part = range_part.strip() or "*"
        if part == "*":
            start, end = minimum, maximum
        elif "-" in part:
            left, right = part.split("-", 1)
            if not left.lstrip("-").isdigit() or not right.lstrip("-").isdigit():
                raise ValueError(f"invalid cron range {part!r}")
            start, end = int(left), int(right)
        else:
            if not part.lstrip("-").isdigit():
                raise ValueError(f"invalid cron value {part!r}")
            start = end = int(part)
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"cron field out of range {raw_part!r}")
        if value < start or value > end:
            continue
        if (value - start) % step == 0:
            return True
    return False


def cron_matches(expr: str, when: datetime) -> bool:
    fields = (expr or "").split()
    if len(fields) != 5:
        raise ValueError(f"cron must have 5 fields: {expr!r}")
    minute, hour, day, month, weekday = fields
    dow = when.isoweekday() % 7
    if not _matches_field(minute, when.minute, 0, 59):
        return False
    if not _matches_field(hour, when.hour, 0, 23):
        return False
    if not _matches_field(day, when.day, 1, 31):
        return False
    if not _matches_field(month, when.month, 1, 12):
        return False
    if _matches_field(weekday, dow, 0, 7):
        return True
    return dow == 0 and _matches_field(weekday, 7, 0, 7)


def is_due(
    *,
    now: datetime | None = None,
    event: str | None = None,
    cron: str | None = None,
) -> bool:
    event_name = event if event is not None else os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name != "schedule":
        return True
    raw = cron if cron is not None else os.environ.get("JOBDIGEST_CRON", "")
    expr = (raw or "").strip() or DEFAULT_CRON
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    slot = scheduled_slot(stamp)
    for offset in range(TICK_MINUTES):
        if cron_matches(expr, slot + timedelta(minutes=offset)):
            return True
    return False


def _emit(due: bool) -> None:
    line = f"due={'true' if due else 'false'}\n"
    path = (os.environ.get("GITHUB_OUTPUT") or "").strip()
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
    sys.stdout.write(line)


def main() -> int:
    try:
        due = is_due()
    except ValueError as exc:
        print(f"cron error {exc}", file=sys.stderr)
        return 1
    _emit(due)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
