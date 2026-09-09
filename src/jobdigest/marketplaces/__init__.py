from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from jobdigest.marketplaces.jobspy_bridge import SITES, scrape_row
from jobdigest.portals import PortalResult


def marketplace_proxies() -> list[str] | None:
    raw = os.environ.get("JOBDIGEST_MARKETPLACE_PROXIES") or ""
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    return parts or None


def run_one(
    row: dict,
    *,
    scrape_fn: Callable[..., Any] | None = None,
) -> PortalResult:
    name = str(row.get("name") or "")
    site = str(row.get("site") or "").strip().lower()
    if site not in SITES:
        return PortalResult(
            status="error",
            error_class="parse",
            detail=f"unknown site: {site or '(empty)'}",
            name=name,
        )
    try:
        jobs = scrape_row(row, proxies=marketplace_proxies(), scrape_fn=scrape_fn)
        return PortalResult(status="ok", jobs=jobs, name=name)
    except ImportError:
        return PortalResult(
            status="error",
            error_class="config",
            detail="python-jobspy not installed",
            name=name,
        )
    except Exception as exc:
        klass = "timeout" if "timeout" in type(exc).__name__.lower() or "timeout" in str(exc).lower() else "http"
        return PortalResult(
            status="error",
            error_class=klass,
            detail=str(exc),
            name=name,
        )
