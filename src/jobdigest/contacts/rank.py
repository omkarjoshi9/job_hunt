from __future__ import annotations

from jobdigest.contacts.email import construct_email
from jobdigest.contacts.types import DiscoveredContact
from jobdigest.normalize import collapse_ws

_SOURCE_RANK = {"jd": 0, "github": 1, "pattern": 2}


def _key(name: str) -> str:
    return collapse_ws(name).casefold()


def merge_contacts(
    *,
    jd: list[DiscoveredContact],
    github: list[DiscoveredContact],
    company: str,
    domain: str | None,
    pattern: str | None,
) -> list[DiscoveredContact]:
    by_name: dict[str, DiscoveredContact] = {}
    for person in list(jd) + list(github):
        name = collapse_ws(person.name)
        if not name:
            continue
        key = _key(name)
        email = person.email
        if not email and pattern:
            email = construct_email(name, pattern)
        source = person.source if person.source in _SOURCE_RANK else "pattern"
        confidence = person.confidence
        if source == "jd" and person.role and "recruit" in (person.role or "").lower():
            confidence = 90.0
        elif source == "jd":
            confidence = 100.0 if confidence < 90 else confidence
        elif source == "github":
            confidence = 70.0
        merged = DiscoveredContact(
            name=name,
            source=source,
            company=company,
            domain=domain,
            email=email,
            role=person.role,
            confidence=confidence,
        )
        existing = by_name.get(key)
        if existing is None:
            by_name[key] = merged
            continue
        if _SOURCE_RANK.get(merged.source, 9) < _SOURCE_RANK.get(existing.source, 9):
            if not merged.email:
                merged.email = existing.email
            by_name[key] = merged
        elif not existing.email and merged.email:
            existing.email = merged.email
            existing.domain = existing.domain or merged.domain

    if pattern and domain and not by_name:
        # constructed-only placeholder is skipped: need a human name
        pass

    return list(by_name.values())
