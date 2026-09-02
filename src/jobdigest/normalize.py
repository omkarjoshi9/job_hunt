from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_DROP_EXACT = {"gclid", "fbclid", "mc_cid", "mc_eid", "_ga", "ref"}


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
