from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

Conn = Any
RowLike = Any


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def now() -> str:
    """Current timestamp, resolved through ``jobdigest.db.utc_now`` at call time.

    Tests (and callers) monkeypatch ``jobdigest.db.utc_now``; going through the
    package attribute keeps that working after the split into submodules.
    """
    import jobdigest.db as pkg

    return pkg.utc_now()


def row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def norm_http_url(raw: str | None) -> str | None:
    from jobdigest.normalize import normalize_url

    text = (raw or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return None
    norm = normalize_url(text)
    if not norm.lower().startswith(("http://", "https://")):
        return None
    return norm
