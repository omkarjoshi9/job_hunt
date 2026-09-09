from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urljoin

from jobdigest.ats.base import NormalizedJob
from jobdigest.portals.generic import (
    css_select,
    first_href,
    first_text,
    jobs_from_cards,
    node_href,
    node_text,
    to_root,
)

_P_INNER = re.compile(r"<p>([\s\S]*?)</p>", re.I)


def _attr(node: Any, name: str) -> str:
    attrib = getattr(node, "attrib", None) or {}
    if hasattr(attrib, "get"):
        return str(attrib.get(name) or "").strip()
    return ""


def _stickie_url(node: Any, base_url: str) -> str:
    raw = node_href(node) or _attr(node, "data-href")
    abs_url = urljoin(base_url, raw)
    if abs_url.lower().startswith("http://") or abs_url.lower().startswith("https://"):
        return abs_url
    return ""


def _as_text(page_or_html: Any) -> str | None:
    if isinstance(page_or_html, bytes):
        return page_or_html.decode("utf-8", errors="replace")
    if isinstance(page_or_html, str):
        return page_or_html
    html = getattr(page_or_html, "html_content", None) or getattr(page_or_html, "html", None)
    if isinstance(html, bytes):
        return html.decode("utf-8", errors="replace")
    if isinstance(html, str):
        return html
    return None


def _http_url(raw: str, base_url: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    abs_url = urljoin(base_url, raw)
    if abs_url.lower().startswith("http://") or abs_url.lower().startswith("https://"):
        return abs_url
    return ""


def _flatten_rows(items: list) -> list:
    rows: list = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("posts"), list):
            rows.extend(item["posts"])
        else:
            rows.append(item)
    return rows


def _json_cards(payload: Any, *, source: str, base_url: str) -> list[dict]:
    rows: list = []
    if isinstance(payload, list):
        rows = _flatten_rows(payload)
    elif isinstance(payload, dict):
        grouped = payload.get("grouped")
        if isinstance(grouped, list):
            rows.extend(_flatten_rows(grouped))
        for key in ("posts", "jobs", "items", "data"):
            val = payload.get(key)
            if isinstance(val, list) and val:
                rows.extend(_flatten_rows(val))
                break
    cards: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(
            row.get("headline") or row.get("title") or row.get("name") or row.get("job_title") or ""
        ).strip()
        raw_url = str(
            row.get("url") or row.get("href") or row.get("link") or row.get("apply_url") or ""
        ).strip()
        url = _http_url(raw_url, base_url)
        if not title or not url or url in seen:
            continue
        seen.add(url)
        company = str(row.get("company_name") or row.get("company") or source).strip() or source
        location = str(row.get("location") or "").strip()
        description = str(row.get("description") or "").strip()
        cards.append(
            {
                "title": title,
                "url": url,
                "company": company,
                "location": location,
                "description": description,
            }
        )
    return cards


def _json_candidate(text: str) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if stripped[0] in "{[":
        return stripped
    # Scrapling HTTP wraps application/json as <html><body><p>{...}</p></body></html>
    match = _P_INNER.search(stripped)
    if not match:
        return None
    inner = unescape(match.group(1)).strip()
    if inner[:1] in "{[":
        return inner
    return None


def _try_json_jobs(page_or_html: Any, *, source: str, base_url: str) -> list[NormalizedJob] | None:
    text = _as_text(page_or_html)
    if text is None:
        return None
    candidate = _json_candidate(text)
    if candidate is None:
        return None
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return jobs_from_cards(_json_cards(payload, source=source, base_url=base_url), source=source)


def parse(page_or_html: Any, *, source: str, base_url: str) -> list[NormalizedJob]:
    json_jobs = _try_json_jobs(page_or_html, source=source, base_url=base_url)
    if json_jobs is not None:
        return json_jobs
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
    for stickie in css_select(root, ".stickie"):
        cards.append(
            {
                "title": first_text(stickie, ".headline"),
                "url": _stickie_url(stickie, base_url),
                "company": first_text(stickie, ".company-name") or source,
                "location": first_text(stickie, ".top-left"),
                "description": node_text(stickie),
            }
        )
    return jobs_from_cards(cards, source=source)
