from __future__ import annotations

from typing import Any

from jobdigest.ats.base import NormalizedJob
from jobdigest.portals.generic import (
    _http_url,
    css_select,
    first_text,
    jobs_from_cards,
    node_href,
    node_text,
    to_root,
)


def parse(page_or_html: Any, *, source: str, base_url: str) -> list[NormalizedJob]:
    root = to_root(page_or_html, base_url)
    cards = []
    for card in css_select(root, "li.job"):
        anchors = css_select(card, "a")
        href = _http_url(node_href(anchors[0]), base_url) if anchors else ""
        title = node_text(anchors[0]) if anchors else ""
        cards.append(
            {
                "title": title,
                "url": href,
                "location": first_text(card, ".meta"),
                "company": source,
                "description": node_text(card),
            }
        )
    return jobs_from_cards(cards, source=source)
