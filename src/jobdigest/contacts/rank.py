from __future__ import annotations

from jobdigest.contacts.email import construct_email
from jobdigest.contacts.types import DiscoveredContact
from jobdigest.normalize import collapse_ws

_SOURCE_RANK = {"jd": 0, "github": 1, "pattern": 2}
# Emails guessed from a domain pattern are never more trustworthy than this.
CONSTRUCTED_EMAIL_MAX_CONFIDENCE = 40.0


def _key(name: str) -> str:
    return collapse_ws(name).casefold()


def _base_confidence(person: DiscoveredContact, source: str) -> float:
    confidence = person.confidence
    if source == "jd" and "recruit" in (person.role or "").lower():
        return 90.0
    if source == "jd":
        return 100.0 if confidence < 90 else confidence
    if source == "github":
        return 70.0
    return confidence


def merge_contacts(
    *,
    jd: list[DiscoveredContact],
    github: list[DiscoveredContact],
    company: str,
    domain: str | None,
    pattern: str | None,
) -> list[DiscoveredContact]:
    """Merge contacts by name. Observed emails keep source confidence; emails built
    from ``pattern`` are flagged ``email_inferred`` and capped at 40."""
    by_name: dict[str, DiscoveredContact] = {}
    for person in list(jd) + list(github):
        name = collapse_ws(person.name)
        if not name:
            continue
        key = _key(name)
        email = person.email
        inferred = False
        if not email and pattern:
            email = construct_email(name, pattern)
            inferred = email is not None
        source = person.source if person.source in _SOURCE_RANK else "pattern"
        confidence = _base_confidence(person, source)
        if inferred:
            confidence = min(confidence, CONSTRUCTED_EMAIL_MAX_CONFIDENCE)
        merged = DiscoveredContact(
            name=name,
            source=source,
            company=company,
            domain=domain,
            email=email,
            role=person.role,
            confidence=confidence,
            email_inferred=inferred,
        )
        existing = by_name.get(key)
        if existing is None:
            by_name[key] = merged
        elif _SOURCE_RANK.get(merged.source, 9) < _SOURCE_RANK.get(existing.source, 9):
            if not merged.email:
                merged.email = existing.email
                merged.email_inferred = existing.email_inferred
            by_name[key] = merged
        elif not existing.email and merged.email:
            existing.email = merged.email
            existing.email_inferred = merged.email_inferred
            existing.domain = existing.domain or merged.domain
    return list(by_name.values())
