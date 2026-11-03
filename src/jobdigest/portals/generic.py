from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin, urlsplit

from jobdigest.ats.base import NormalizedJob, decode_html_content


def _http_url(url: str, base: str) -> str:
    abs_url = urljoin(base, (url or "").strip())
    if abs_url.lower().startswith("http://") or abs_url.lower().startswith("https://"):
        return abs_url
    return ""


def jobs_from_cards(
    cards: list[dict],
    *,
    source: str,
    ats: str = "portal",
) -> list[NormalizedJob]:
    jobs: list[NormalizedJob] = []
    for card in cards:
        apply_url = str(card.get("url") or "")
        if not (
            apply_url.lower().startswith("http://")
            or apply_url.lower().startswith("https://")
        ):
            continue
        title = str(card.get("title") or "").strip()
        if not title:
            continue
        path = urlsplit(apply_url).path.strip("/")
        jobs.append(
            NormalizedJob(
                title=title,
                company=str(card.get("company") or source),
                location_raw=str(card.get("location") or ""),
                apply_url=apply_url,
                hosted_url=apply_url,
                employment_hint=None,
                workplace_hint=None,
                description_text=decode_html_content(str(card.get("description") or "")),
                source=source,
                ats=ats,
                external_id=path,
                raw_json=json.dumps(card, separators=(",", ":"), ensure_ascii=False),
            )
        )
    return jobs


def to_root(page_or_html: Any, base_url: str = ""):
    if page_or_html is not None and not isinstance(page_or_html, (str, bytes)):
        if hasattr(page_or_html, "css"):
            return page_or_html
    from scrapling.parser import Selector

    html = page_or_html
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    return Selector(str(html or ""), url=base_url, adaptive=False)


def css_select(node: Any, selector: str) -> list[Any]:
    css = getattr(node, "css", None)
    if not callable(css):
        return []
    found = css(selector, adaptive=False)
    return [] if found is None else list(found)


def node_text(node: Any) -> str:
    if node is None:
        return ""
    text = getattr(node, "text", None)
    if text:
        return str(text).strip()
    get_all = getattr(node, "get_all_text", None)
    if callable(get_all):
        return str(get_all(separator=" ", strip=True)).strip()
    return str(node).strip()


def node_href(node: Any) -> str:
    if node is None:
        return ""
    attrib = getattr(node, "attrib", None) or {}
    href = attrib.get("href") if hasattr(attrib, "get") else ""
    return str(href or "").strip()


def first_text(node: Any, selector: str) -> str:
    found = css_select(node, selector)
    if not found:
        return ""
    return node_text(found[0])


def first_href(node: Any, selector: str, *, base: str) -> str:
    found = css_select(node, selector)
    if not found:
        return ""
    return _http_url(node_href(found[0]), base)


def parse(page_or_html: Any, *, source: str, base_url: str) -> list[NormalizedJob]:
    root = to_root(page_or_html, base_url)
    cards: list[dict] = []
    seen: set[str] = set()
    for selector in ("article", '[class*="job"]', "li"):
        for node in css_select(root, selector):
            href = ""
            title = first_text(node, "h2") or first_text(node, "h3")
            for anchor in css_select(node, "a[href]"):
                candidate = _http_url(node_href(anchor), base_url)
                if candidate:
                    href = candidate
                    if not title:
                        title = node_text(anchor)
                    break
            if not href or href in seen:
                continue
            seen.add(href)
            cards.append(
                {
                    "title": title,
                    "url": href,
                    "company": source,
                    "location": "",
                    "description": node_text(node),
                }
            )
    return jobs_from_cards(cards, source=source)
