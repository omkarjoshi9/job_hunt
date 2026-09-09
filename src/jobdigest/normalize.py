from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_DROP_EXACT = {"gclid", "fbclid", "mc_cid", "mc_eid", "_ga", "ref"}
_WS = re.compile(r"\s+")


def collapse_ws(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def description_hash(text: str | None) -> str:
    return hashlib.sha256(collapse_ws(text).encode("utf-8")).hexdigest()


_LEGAL_TRAIL = re.compile(
    r"(?:,|\s)+(?:"
    r"private\s+limited|"
    r"pvt\.?\s*ltd\.?|"
    r"corporation|"
    r"limited|"
    r"private|"
    r"software|"
    r"gmbh|"
    r"llp|"
    r"llc|"
    r"plc|"
    r"inc|"
    r"corp|"
    r"ltd|"
    r"pvt"
    r")\.?\s*$",
    re.I,
)
_CITIES = (
    "pune|bengaluru|bangalore|hyderabad|mumbai|chennai|"
    "noida|gurugram|gurgaon|delhi|ncr|india"
)
_TITLE_PAREN_CITY = re.compile(rf"\s*\((?:{_CITIES})(?:,[^)]*)?\)", re.I)
_TITLE_TRAIL_CITY = re.compile(rf"\s*[-–|/]\s*(?:{_CITIES})\s*$", re.I)
_TITLE_YEAR = re.compile(r"\b20\d\d\b")
_TITLE_CAMPUS = re.compile(r"\b(?:off[-\s]?campus|on[-\s]?campus|campus)\b", re.I)
_LOC_ALIAS = {"bangalore": "bengaluru", "gurgaon": "gurugram"}


def normalize_company(name: str | None) -> str:
    text = collapse_ws(name)
    if not text:
        return ""
    text = text.replace("&", " and ")
    text = collapse_ws(text)
    prev = None
    while prev != text:
        prev = text
        text = collapse_ws(_LEGAL_TRAIL.sub("", text))
    return text


def normalize_title(title: str | None) -> str:
    text = collapse_ws(title)
    if not text:
        return ""
    text = _TITLE_PAREN_CITY.sub("", text)
    text = _TITLE_TRAIL_CITY.sub("", text)
    text = _TITLE_YEAR.sub(" ", text)
    text = _TITLE_CAMPUS.sub(" ", text)
    return collapse_ws(text)


def normalize_location(location_raw: str | None) -> str:
    text = collapse_ws(location_raw)
    if not text:
        return ""
    text = text.split("|", 1)[0]
    text = collapse_ws(text).casefold()
    return _LOC_ALIAS.get(text, text)


def posting_key(title: str, company: str, location_raw: str) -> str | None:
    title_n = normalize_title(title).casefold()
    company_n = normalize_company(company).casefold()
    loc_n = normalize_location(location_raw)
    if not title_n or not company_n:
        return None
    blob = f"{title_n}\n{company_n}\n{loc_n}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parts.port:
        netloc = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _DROP_EXACT and not k.lower().startswith("utm_")
    ]
    query = urlencode(kept)
    scheme = (parts.scheme or "https").lower()
    return urlunsplit((scheme, netloc, path, query, ""))


def job_identity(
    apply_url: str, hosted_url: str, source: str, external_id: str
) -> tuple[str | None, str | None]:
    """Return (apply_url_for_column, apply_url_norm) or (None, None) to skip insert.

    apply_url_for_column is an http(s) URL or None (synthetic key is not clickable).
    """
    candidate = (apply_url or "").strip() or (hosted_url or "").strip()
    ext = (external_id or "").strip()
    src = (source or "").strip()
    if candidate.lower().startswith("http://") or candidate.lower().startswith("https://"):
        return candidate, normalize_url(candidate)
    if src and ext:
        return None, f"ats://{src}/{ext}"
    return None, None
