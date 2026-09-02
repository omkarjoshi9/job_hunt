from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import feedparser
import httpx

from jobdigest.ats.base import NormalizedJob, decode_html_content
from jobdigest.portals import PortalResult
from jobdigest.portals.session import UA


def parse_feed(xml_text: str, *, source: str) -> list[NormalizedJob]:
    parsed = feedparser.parse(xml_text or "")
    jobs: list[NormalizedJob] = []
    for entry in getattr(parsed, "entries", []) or []:
        link = _entry_link(entry)
        if not (
            link.lower().startswith("http://") or link.lower().startswith("https://")
        ):
            continue
        title = str(getattr(entry, "title", None) or "").strip()
        if not title:
            continue
        desc = str(
            getattr(entry, "summary", None)
            or getattr(entry, "description", None)
            or ""
        )
        ext = _entry_id(entry)
        if not ext:
            ext = urlsplit(link).path.strip("/")
        card = {"title": title, "url": link, "description": desc}
        jobs.append(
            NormalizedJob(
                title=title,
                company=source,
                location_raw="",
                apply_url=link,
                hosted_url=link,
                employment_hint=None,
                workplace_hint=None,
                description_text=decode_html_content(desc),
                source=source,
                ats="rss",
                external_id=str(ext),
                raw_json=json.dumps(card, separators=(",", ":"), ensure_ascii=False),
            )
        )
    return jobs


def _entry_link(entry: Any) -> str:
    link = str(getattr(entry, "link", None) or "").strip()
    if link:
        return link
    for item in getattr(entry, "links", None) or []:
        href = item.get("href") if isinstance(item, dict) else getattr(item, "href", "")
        if href:
            return str(href).strip()
    return ""


def _entry_id(entry: Any) -> str:
    for key in ("id", "guid"):
        val = getattr(entry, key, None)
        if val is None and hasattr(entry, "get"):
            val = entry.get(key)
        if val is None:
            continue
        if isinstance(val, dict):
            val = val.get("value") or val.get("guid") or ""
        text = str(val).strip()
        if text:
            return text
    return ""


def fetch_feed(url: str, *, http_get: Callable[[str], Any] | None = None) -> str:
    if http_get is None:
        response = httpx.get(
            url, headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True
        )
        response.raise_for_status()
        return response.text
    response = http_get(url)
    if isinstance(response, str):
        return response
    text = getattr(response, "text", None)
    if text is not None:
        return str(text)
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return str(response)


def run_one(
    feed: dict,
    *,
    http_get: Callable[[str], Any] | None = None,
) -> PortalResult:
    name = str(feed.get("name") or "")
    url = str(feed.get("url") or "")
    try:
        xml = fetch_feed(url, http_get=http_get)
        jobs = parse_feed(xml, source=name)
        return PortalResult(status="ok", jobs=jobs, http_status=200, name=name, url=url)
    except Exception as exc:
        klass = "timeout" if "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower() else "http"
        return PortalResult(
            status="error",
            error_class=klass,
            detail=str(exc),
            name=name,
            url=url,
        )
