from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from jobdigest.ats.base import NormalizedJob
from jobdigest.portals.generic import (
    css_select,
    first_href,
    first_text,
    jobs_from_cards,
    node_text,
    to_root,
)


def _attr(node: Any, name: str) -> str:
    attrib = getattr(node, "attrib", None) or {}
    if hasattr(attrib, "get"):
        return str(attrib.get(name) or "").strip()
    return ""


def parse(page_or_html: Any, *, source: str, base_url: str) -> list[NormalizedJob]:
    root = to_root(page_or_html, base_url)
    cards = []
    for card in css_select(root, ".job-card"):
        cards.append(
            {
                "title": first_text(card, ".job-title"),
                "url": first_href(card, "a.apply", base=base_url),
                "location": first_text(card, ".job-location"),
                "company": source,
                "description": node_text(card),
            }
        )
    for item in css_select(root, ".job-item"):
        href = first_href(item, "a", base=base_url)
        if not href:
            href = urljoin(base_url, _attr(item, "data-route"))
        title = first_text(item, "h6") or _attr(item, "data-title")
        cards.append(
            {
                "title": title,
                "url": href,
                "location": _attr(item, "data-location"),
                "company": _attr(item, "data-company-display") or source,
                "description": node_text(item),
            }
        )
    return jobs_from_cards(cards, source=source)
