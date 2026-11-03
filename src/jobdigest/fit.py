"""Deterministic job-fit scoring (0-100).

The LLM tends to give every plausible job the same high score, so ranking leans on
this code-owned score instead: role family, entry-level signal (new grad first),
skill overlap with the profile, source/company trust, and real posting freshness.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from jobdigest.config import Profile
from jobdigest.hard_filter import low_priority_function
from jobdigest.tiers import is_top_company

FIT_WEIGHT = 0.65
LLM_WEIGHT = 0.35

_CORE_ROLE = re.compile(
    r"back[- ]?end|\bml\b|machine learning|\bai\b|\bllm\b|\bgenai\b|security|"
    r"platform|infra\w*|distributed|systems?|\bdata\b|devops|\bsre\b|cloud|"
    r"research\w*|scientist|compiler|kernel|database",
    re.I,
)
_FULLSTACK = re.compile(r"full[- ]?stack", re.I)
_GENERIC_SWE = re.compile(r"software|\bsde\b|\bswe\b|developer|engineer|programmer", re.I)
_NEW_GRAD = re.compile(
    r"new[- ]?grad|graduate|\bsde[- ]?(?:1|i)\b|engineer[- ]?i\b|fresher|"
    r"early[- ]career|entry[- ]level|campus",
    re.I,
)
_INTERN = re.compile(r"\bintern(?:ship)?s?\b|trainee|apprentice", re.I)
_ENTRY_JD = re.compile(
    r"\bfreshers?\b|new[- ]?grad|recent graduates?|entry[- ]level|"
    r"0\s*(?:[-–]|to)\s*1\s*years?|class of 20\d\d|"
    r"graduat(?:e|ing|ion)\W+(?:in\W+)?20\d\d",
    re.I,
)
_RESPONSIBILITIES = re.compile(
    r"responsibilit|what you.?ll do|you will|day[- ]to[- ]day|in this role", re.I
)

_SKILL_ALIASES = {
    "rag": ("rag", "retrieval augmented", "retrieval-augmented"),
    "langchain": ("langchain", "langgraph"),
    "langgraph": ("langchain", "langgraph"),
    "aws": ("aws", "amazon web services"),
    "gcp": ("gcp", "google cloud"),
    "security": ("security", "appsec", "infosec", "cybersecurity"),
    "ml": ("machine learning", "ml"),
}


def _row(row: Any, key: str) -> str:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return ""
    return "" if value is None else str(value)


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _word(term: str, text: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text) is not None


def role_points(title: str, company: str, profile: Profile) -> float:
    if low_priority_function(title):
        return 14.0  # frontend/QA/mobile: only reaches here at top companies
    if _CORE_ROLE.search(title):
        return 30.0
    if _FULLSTACK.search(title):
        return 26.0
    if _GENERIC_SWE.search(title):
        return 22.0
    return 12.0


def level_points(title: str, description: str) -> float:
    if _NEW_GRAD.search(title):
        return 20.0  # new grad is the priority
    if _INTERN.search(title):
        return 15.0
    if _ENTRY_JD.search(description):
        return 13.0
    return 4.0


def skill_points(profile: Profile, title: str, description: str) -> float:
    hay = f"{title}\n{description}".lower()
    matched = 0
    for skill in profile.skills:
        key = skill.strip().lower()
        if not key:
            continue
        variants = _SKILL_ALIASES.get(key, (key,))
        if any(_word(v, hay) for v in variants):
            matched += 1
    return 25.0 * min(matched, 4) / 4.0


def trust_points(row: Any, profile: Profile) -> float:
    company = _row(row, "company") or _row(row, "display_name")
    points = 0.0
    if is_top_company(company, profile.top_companies):
        points += 8.0
    watch = {w.strip().casefold() for w in profile.watchlist if w.strip()}
    if watch and company.strip().casefold() in watch:
        points += 7.0
    ats = _row(row, "ats").lower()
    points += 4.0 if ats not in {"portal", "rss", "marketplace"} else 1.0
    description = _row(row, "description_text")
    if len(description) >= 600 and _RESPONSIBILITIES.search(description):
        points += 3.0
    return min(points, 15.0)


def freshness_points(row: Any, now: datetime) -> float:
    stamp = _parse_iso(_row(row, "posted_at")) or _parse_iso(_row(row, "first_seen_at"))
    if stamp is None:
        return 3.0
    when = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (when - stamp).total_seconds() / 86400.0)
    if age_days <= 3:
        return 10.0
    if age_days <= 7:
        return 7.0
    if age_days <= 14:
        return 4.0
    if age_days <= 30:
        return 2.0
    return 0.0


def fit_score(row: Any, profile: Profile, now: datetime) -> float:
    title = _row(row, "title")
    description = _row(row, "description_text")
    company = _row(row, "company")
    total = (
        role_points(title, company, profile)
        + level_points(title, description)
        + skill_points(profile, title, description)
        + trust_points(row, profile)
        + freshness_points(row, now)
    )
    return max(0.0, min(100.0, total))


def quality_score(row: Any, profile: Profile, now: datetime) -> float:
    """Blend of deterministic fit and the LLM score, both on 0-100."""
    llm = float(_row(row, "score") or 0)
    return FIT_WEIGHT * fit_score(row, profile, now) + LLM_WEIGHT * llm
