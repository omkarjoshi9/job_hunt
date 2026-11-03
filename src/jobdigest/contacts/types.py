from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DiscoveredContact:
    name: str
    source: str
    company: str = ""
    domain: str | None = None
    email: str | None = None
    role: str | None = None
    confidence: float = 0.0
    # True when `email` was built from a domain pattern rather than observed.
    email_inferred: bool = False
