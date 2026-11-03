from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _ascii_token(text: str) -> str:
    """Lowercase, strip diacritics and drop punctuation (O'Brien -> obrien)."""
    folded = unicodedata.normalize("NFKD", text or "")
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").lower()
    return _NON_ALNUM.sub("", ascii_text)


def _name_parts(name: str) -> tuple[str, str, str, str]:
    parts = [p for p in (_ascii_token(w) for w in (name or "").split()) if p]
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) > 1 else ""
    return first, last, first[:1], last[:1]


def construct_email(name: str, pattern: str) -> str | None:
    first, last, fi, li = _name_parts(name)
    if not first:
        return None
    try:
        email = pattern.format(first=first, last=last, fi=fi, li=li)
    except (KeyError, ValueError, IndexError, AttributeError):
        return None
    if "{" in email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return None
    return email.lower()
