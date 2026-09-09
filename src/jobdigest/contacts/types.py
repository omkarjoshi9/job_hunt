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
