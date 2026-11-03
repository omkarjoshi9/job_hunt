from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from jobdigest.config import Profile
from jobdigest.fit import quality_score
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
    profile: Profile | None = None,
) -> float:
    """Ranking key. With a profile: fit/LLM quality (freshness included) + location
    + prior. Without one: the legacy LLM score + first-seen recency."""
    if profile is not None:
        base = quality_score(row, profile, now)
    else:
        base = float(row["score"] or 0) + recency_boost(row["first_seen_at"], now)
    return (
        base
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
    profile: Profile | None = None,
) -> list[RowLike]:
    return sorted(
        rows,
        key=lambda r: (
            -blend_rank(
                r, home=home, relocate=relocate, now=now, priors=priors, profile=profile
            ),
            int(r["id"]),
        ),
    )


COOLDOWN_BYPASS_QUALITY = 85.0


def company_key(name: Any) -> str:
    return normalize_company(str(name or "")).casefold()


_NAME_SUFFIXES = frozenset(
    {"us", "usa", "uk", "eu", "india", "in", "global", "inc", "llc", "ltd", "corp",
     "limited", "pvt", "private", "gmbh", "sa", "emea", "apac"}
)


def _alias(key: str, known: Collection[str]) -> str:
    """Map "episode six us" onto an already-seen "episode six".

    Only region/legal suffixes merge; a real sub-brand ("google cloud india" vs
    "google") stays a separate company.
    """
    words = key.split()
    for other in known:
        o = other.split()
        long_, short = (words, o) if len(words) >= len(o) else (o, words)
        if long_[: len(short)] == short and set(long_[len(short):]) <= _NAME_SUFFIXES:
            return other
    return key


def _age_days(row: RowLike, now: datetime) -> float | None:
    for key in ("posted_at", "first_seen_at"):
        try:
            raw = row[key]
        except (KeyError, IndexError, TypeError):
            continue
        dt = _try_parse_iso(str(raw)) if raw and str(raw).strip() else None
        if dt is not None:
            when = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
            return max(0.0, (when - dt).total_seconds() / 86400.0)
    return None


def select_diverse(
    rows: Sequence[RowLike],
    limit: int,
    *,
    profile: Profile,
    now: datetime,
    recent_companies: Collection[str] = (),
) -> list[RowLike]:
    """Pick the best rows (already sorted) for a digest.

    Drops stale postings and anything below the quality bar, keeps at most
    ``digest_per_company`` roles per company (and ``digest_per_portal`` per
    aggregator), and skips companies already sent within the cooldown unless the
    job is exceptional. Returns fewer rows rather than padding.
    """
    chosen: list[RowLike] = []
    per_company: dict[str, int] = {}
    per_portal: dict[str, int] = {}
    cooling = {company_key(c) for c in recent_companies}
    for row in rows:
        if len(chosen) >= max(int(limit), 0):
            break
        age = _age_days(row, now)
        if age is not None and age > profile.digest_max_age_days:
            continue
        quality = quality_score(row, profile, now)
        if quality < profile.digest_min_quality:
            continue
        ats = str(row["ats"] or "").lower()
        if ats in {"portal", "rss", "marketplace"}:
            key = str(row["source"] or "")
            if per_portal.get(key, 0) >= profile.digest_per_portal:
                continue
            per_portal[key] = per_portal.get(key, 0) + 1
        else:
            key = _alias(company_key(row["company"]), [*per_company, *cooling])
            if key and key in cooling and quality < COOLDOWN_BYPASS_QUALITY:
                continue
            if key and per_company.get(key, 0) >= profile.digest_per_company:
                continue
            per_company[key] = per_company.get(key, 0) + 1
        chosen.append(row)
    return chosen
