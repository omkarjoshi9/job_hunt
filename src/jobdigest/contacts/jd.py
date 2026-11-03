from __future__ import annotations

import re

from jobdigest.contacts.types import DiscoveredContact
from jobdigest.normalize import collapse_ws

_PART = r"[A-Z][a-z]+(?:['-][A-Za-z]+)?"
_NAME = rf"({_PART}(?:\s+{_PART}){{0,2}})"
_LABELS: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(rf"(?i)reports\s+to[:\s]+(?-i:{_NAME})"), "Hiring Manager", 100.0),
    (re.compile(rf"(?i)hiring\s+manager[:\s]+(?-i:{_NAME})"), "Hiring Manager", 100.0),
    (re.compile(rf"(?i)recruiter[:\s]+(?-i:{_NAME})"), "Recruiter", 90.0),
    (re.compile(rf"(?i)HR\s+contact[:\s]+(?-i:{_NAME})"), "Recruiter", 90.0),
)
_TRAIL_PUNCT = re.compile(r"[.,;:]+$")
_STOP = frozenset(
    {
        "the",
        "this",
        "you",
        "after",
        "team",
        "intern",
        "role",
        "hiring",
        "recruiter",
        "contact",
        "hr",
        "manager",
        "reports",
    }
)
_TITLE_WORDS = frozenset(
    {
        "senior", "junior", "staff", "principal", "engineer", "engineers",
        "manager", "managers", "lead", "leads", "director", "head", "team",
        "teams", "vp", "president", "developer", "officer", "analyst",
        "associate", "architect", "scientist", "specialist", "coordinator",
        "executive", "founder", "cto", "ceo", "intern", "interns",
    }
)
_DEGREE_FALSE = re.compile(
    r"\b("
    r"bachelor|bachelors|master|masters|phd|mba|degree|"
    r"university|college|science|department|faculty|school|"
    r"engineering|arts|honours|honors|computer"
    r")\b",
    re.I,
)


def _clean_name(name: str) -> str:
    text = _TRAIL_PUNCT.sub("", collapse_ws(name)).strip()
    parts = [p for p in text.split() if p.casefold() not in _STOP]
    return collapse_ws(" ".join(parts))


def _valid_name(name: str) -> bool:
    text = _clean_name(name)
    if not text or len(text.split()) > 3:
        return False
    if _DEGREE_FALSE.search(text):
        return False
    if any(part.casefold() in _TITLE_WORDS for part in text.split()):
        return False
    return True


def parse_jd_names(description_text: str | None) -> list[DiscoveredContact]:
    text = description_text or ""
    found: list[DiscoveredContact] = []
    seen: set[str] = set()
    for pattern, role, confidence in _LABELS:
        for match in pattern.finditer(text):
            name = _clean_name(match.group(1))
            if not _valid_name(name):
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(
                DiscoveredContact(
                    name=name,
                    source="jd",
                    role=role,
                    confidence=confidence,
                )
            )
    return found
