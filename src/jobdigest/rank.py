from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from jobdigest.normalize import normalize_company

RECENCY_WINDOW_DAYS = 7.0
RECENCY_MAX = 14.0
RECENCY_PER_DAY = RECENCY_MAX / RECENCY_WINDOW_DAYS
LOC_HOME = 8.0
LOC_RELOCATE = 4.0
LOC_OTHER = 0.0
PRIOR_POSITIVE = 5.0
PRIOR_MAX = 5.0

_ALIASES = {
    "bengaluru": ("bengaluru", "bangalore", "bengalooru"),
    "bangalore": ("bengaluru", "bangalore", "bengalooru"),
    "bengalooru": ("bengaluru", "bangalore", "bengalooru"),
}

RowLike = Any


def _try_parse_iso(value: str) -> datetime | None:
    try:
        text = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _needles(city: str) -> tuple[str, ...]:
    key = (city or "").strip().casefold()
    if not key:
        return ()
    return _ALIASES.get(key, (key,))


def _hits(hay: str, city: str) -> bool:
    return any(
        re.search(r"\b" + re.escape(needle) + r"\b", hay) is not None
        for needle in _needles(city)
    )


def age_chip(first_seen_at: str | None, now: datetime) -> str:
    if not first_seen_at or not str(first_seen_at).strip():
        return ""
    dt = _try_parse_iso(str(first_seen_at))
    if dt is None:
        return ""
    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    age_seconds = (now_utc - dt).total_seconds()
    if age_seconds < 3600:
        return "<1h"
    if age_seconds < 86400:
        return f"{int(age_seconds // 3600)}h"
    return f"{int(age_seconds // 86400)}d"


def source_chip(ats: str | None) -> str:
    key = (ats or "").strip().casefold()
    if key == "rss":
        return "rss"
    if key == "portal":
        return "portal"
    if key == "marketplace":
        return "marketplace"
    return "ats"


def recency_boost(first_seen_at: str | None, now: datetime) -> float:
    if not first_seen_at or not str(first_seen_at).strip():
        return 0.0
    dt = _try_parse_iso(str(first_seen_at))
    if dt is None:
        return 0.0
    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    age_days = max(0.0, (now_utc - dt).total_seconds() / 86400.0)
    return max(0.0, RECENCY_WINDOW_DAYS - age_days) * RECENCY_PER_DAY


def location_pref(
    location_raw: str,
    title: str,
    *,
    home: str,
    relocate: Sequence[str],
) -> float:
    hay = f"{location_raw or ''}\n{title or ''}".casefold()
    if _hits(hay, home):
        return LOC_HOME
    for city in relocate:
        if _hits(hay, city):
            return LOC_RELOCATE
    return LOC_OTHER


def _row_text(row: RowLike, key: str) -> str:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return ""
    if value is None:
        return ""
    return str(value)


def company_prior(row: RowLike, priors: Mapping[str, float]) -> float:
    if not priors:
        return 0.0
    keys: list[str] = []
    company_key = normalize_company(_row_text(row, "company")).casefold()
    if company_key:
        keys.append(company_key)
    display_key = normalize_company(_row_text(row, "display_name")).casefold()
    if display_key and display_key not in keys:
        keys.append(display_key)
    for key in keys:
        if key not in priors:
            continue
        try:
            raw = float(priors[key])
        except (TypeError, ValueError):
            return 0.0
        if raw < 0.0:
            return 0.0
        return min(raw, PRIOR_MAX)
    return 0.0


def blend_rank(
    row: RowLike,
    *,
    home: str,
    relocate: Sequence[str],
    now: datetime,
    priors: Mapping[str, float] | None = None,
) -> float:
    llm = float(row["score"] or 0)
    return (
        llm
        + recency_boost(row["first_seen_at"], now)
        + location_pref(
            row["location_raw"] or "",
            row["title"] or "",
            home=home,
            relocate=relocate,
        )
        + company_prior(row, priors or {})
    )


def sort_rows(
    rows: Sequence[RowLike],
    *,
    home: str,
    relocate: Sequence[str],
    now: datetime,
    priors: Mapping[str, float] | None = None,
) -> list[RowLike]:
    return sorted(
        rows,
        key=lambda r: (
            -blend_rank(r, home=home, relocate=relocate, now=now, priors=priors),
            int(r["id"]),
        ),
    )
