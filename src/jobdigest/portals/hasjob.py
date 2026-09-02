from __future__ import annotations

from typing import Any

from jobdigest.ats.base import NormalizedJob
from jobdigest.portals.generic import (
    css_select,
    first_href,
    first_text,
    jobs_from_cards,
    node_text,
    to_root,
)


def parse(page_or_html: Any, *, source: str, base_url: str) -> list[NormalizedJob]:
    root = to_root(page_or_html, base_url)
    cards = []
    for card in css_select(root, "div.job-card"):
        cards.append(
            {
                "title": first_text(card, "a.job-title"),
                "url": first_href(card, "a.job-title", base=base_url),
                "company": first_text(card, ".job-company") or source,
                "location": first_text(card, ".job-location"),
                "description": node_text(card),
            }
        )
    return jobs_from_cards(cards, source=source)
