from __future__ import annotations

import re


def _name_parts(name: str) -> tuple[str, str, str, str]:
    parts = (name or "").strip().lower().split()
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) > 1 else ""
    return first, last, first[:1], last[:1]


def construct_email(name: str, pattern: str) -> str | None:
    first, last, fi, li = _name_parts(name)
    if not first:
        return None
    try:
        email = pattern.format(first=first, last=last, fi=fi, li=li)
    except (KeyError, ValueError, IndexError):
        return None
    except Exception:
        return None
    if "{" in email or not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
        return None
    return email.lower()
