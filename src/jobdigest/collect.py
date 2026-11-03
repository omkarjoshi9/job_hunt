from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from jobdigest.ats import get_adapter
from jobdigest.ats.base import (
    KNOWN_ATS,
    CompanyConfig,
    FetchResult,
    FetchStatus,
    NormalizedJob,
)
from jobdigest.ats.http_client import make_client
from jobdigest.config import (
    load_companies,
    load_feeds,
    load_marketplaces,
    load_portals,
    load_profile,
)
from jobdigest.db import (
    bump_unseen_for_source,
    connect,
    finish_run,
    insert_portal_error,
    insert_run,
    job_hard_status,
    job_seen_key,
    update_company_fetch,
    upsert_company,
    upsert_job,
)
from jobdigest.health import HEALTHY, collect_health, format_health
from jobdigest.normalize import job_identity
from jobdigest.envfile import load_dotenv
from jobdigest.logutil import configure, get_logger
from jobdigest.telegram import deliver as telegram_deliver

log = get_logger("collect")

FetchFn = Callable[
    [CompanyConfig, httpx.Client | None, Callable[[float], None]],
    FetchResult,
]
TelegramHttpPost = Callable[..., object]


def _row_company(row: dict) -> CompanyConfig:
    return CompanyConfig(
        id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        ats=str(row.get("ats") or ""),
        token=str(row.get("token") or ""),
        enabled=bool(row.get("enabled", False)),
        notes=str(row.get("notes") or ""),
    )


def _board_url(company: CompanyConfig) -> str:
    token = company.token
    if company.ats == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    if company.ats == "lever":
        return f"https://api.lever.co/v0/postings/{token}?mode=json"
    if company.ats == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    if company.ats == "workable":
        return f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
    if company.ats == "smartrecruiters":
        return f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
    if company.ats == "bamboohr":
        return f"https://{token}.bamboohr.com/careers/list"
    return f"ats://{company.ats}/{token}"


def _dead_streak(conn, company_id: int, status: FetchStatus) -> int:
    row = conn.execute(
        "SELECT dead_streak FROM companies WHERE id = ?",
        (company_id,),
    ).fetchone()
    cur = int(row["dead_streak"] or 0) if row is not None else 0
    if status == FetchStatus.DEAD_TOKEN:
        return cur + 1
    if status in (FetchStatus.OK, FetchStatus.EMPTY_BOARD):
        return 0
    return cur


def _default_fetch(
    company: CompanyConfig,
    client: httpx.Client | None,
    sleep: Callable[[float], None],
) -> FetchResult:
    if client is None:
        raise RuntimeError("httpx client required for ATS fetch")
    adapter = get_adapter(company.ats, client, sleep=sleep)
    return adapter.fetch(company)


def _error_class(detail: str | None) -> str:
    text = (detail or "").lower()
    if "timeout" in text:
        return "timeout"
    return "http"


def _dict_rows(payload: Any, key: str) -> list[dict]:
    data = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _result_ok(result: Any) -> bool:
    return str(getattr(result, "status", "")).lower() == "ok"


def _result_jobs(result: Any) -> list[NormalizedJob]:
    jobs = getattr(result, "jobs", None)
    if not jobs:
        return []
    return list(jobs)


def _hard_row(conn, job_id: int) -> tuple[str, str]:
    row = conn.execute(
        "SELECT hard_status, hard_reason FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return job_hard_status(conn, job_id), ""
    return str(row["hard_status"] or ""), str(row["hard_reason"] or "")


def _seen_keys(jobs: list[NormalizedJob]) -> set[str]:
    keys: set[str] = set()
    for job in jobs:
        ext = (job.external_id or "").strip()
        if ext:
            key = job_seen_key(external_id=ext, apply_url_norm=None)
        else:
            _col, norm = job_identity(
                job.apply_url, job.hosted_url, job.source, job.external_id
            )
            _ = _col
            key = job_seen_key(external_id=None, apply_url_norm=norm)
        if key:
            keys.add(key)
    return keys


def _sweep_source(
    conn,
    source: str,
    jobs: list[NormalizedJob],
    stats: dict[str, int],
    *,
    success: bool,
) -> None:
    result = bump_unseen_for_source(
        conn, source, _seen_keys(jobs), success=success
    )
    stats["missed"] += int(result["missed"])
    stats["tombstoned"] += int(result["tombstoned"])


def _upsert_jobs(
    conn,
    profile,
    jobs: list[NormalizedJob],
    stats: dict[str, int],
    *,
    company_id: int | None = None,
) -> None:
    stats["fetched"] += len(jobs)
    for job in jobs:
        action, job_id = upsert_job(
            conn,
            profile,
            apply_url=job.apply_url,
            hosted_url=job.hosted_url,
            source=job.source,
            external_id=job.external_id,
            company_id=company_id,
            company=job.company,
            title=job.title,
            location_raw=job.location_raw,
            description_text=job.description_text,
            raw_json=job.raw_json,
            ats=job.ats,
            employment_hint=job.employment_hint,
            workplace_hint=job.workplace_hint,
            posted_at=job.posted_at,
        )
        if action == "skip" or job_id is None:
            stats["skipped"] += 1
            log.debug("job skip company=%s title=%s", job.company, job.title)
            continue
        if action == "insert":
            stats["inserted"] += 1
        else:
            stats["updated"] += 1
        hard_status, hard_reason = _hard_row(conn, job_id)
        if hard_status == "pass":
            stats["pass"] += 1
        else:
            stats["reject"] += 1
        log.debug(
            "job %s id=%s company=%s title=%s hard_status=%s hard_reason=%s",
            action,
            job_id,
            job.company,
            job.title,
            hard_status,
            hard_reason,
        )


_ZERO_STATS = {
    "fetched": 0,
    "inserted": 0,
    "updated": 0,
    "skipped": 0,
    "pass": 0,
    "reject": 0,
    "errors": 0,
    "missed": 0,
    "tombstoned": 0,
}


class _Collector:
    """Per-run state shared by the ATS, portal, feed and marketplace lanes."""

    def __init__(self, conn, run_id: int, profile) -> None:
        self.conn = conn
        self.run_id = run_id
        self.profile = profile
        self.stats: dict[str, int] = dict(_ZERO_STATS)
        self.enabled_units = 0
        self.error_units = 0

    def record_error(
        self,
        name: str,
        url: str,
        detail: str,
        *,
        error_class: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.error_units += 1
        self.stats["errors"] += 1
        insert_portal_error(
            self.conn,
            run_id=self.run_id,
            portal_name=name,
            url=url,
            error_class=error_class or _error_class(detail),
            http_status=http_status,
            detail=detail,
        )

    def ats_company(
        self,
        company: CompanyConfig,
        company_id: int,
        result: FetchResult,
    ) -> None:
        """Persist one ATS board fetch (jobs, company health, sweep)."""
        conn = self.conn
        status = result.status
        complete = bool(getattr(result, "complete", True))
        board_url = _board_url(company)
        error = {
            FetchStatus.DEAD_TOKEN: "dead_token",
            FetchStatus.OK: None,
            FetchStatus.EMPTY_BOARD: None,
        }.get(status, result.error or "error")
        update_company_fetch(
            conn,
            company_id,
            http_status=result.http_status,
            job_count=len(result.jobs) if status == FetchStatus.OK else 0,
            error=error,
            resolved_url=board_url,
            dead_streak=_dead_streak(conn, company_id, status),
        )
        if status == FetchStatus.OK:
            _upsert_jobs(
                conn, self.profile, result.jobs, self.stats, company_id=company_id
            )
            _sweep_source(conn, company.id, result.jobs, self.stats, success=complete)
        elif status == FetchStatus.EMPTY_BOARD:
            _sweep_source(conn, company.id, [], self.stats, success=complete)
        elif status != FetchStatus.DEAD_TOKEN:
            self.record_error(
                company.id,
                board_url,
                result.error or "error",
                http_status=result.http_status,
            )

    def side_unit(
        self,
        kind: str,
        row: dict,
        run_fn: Callable[[dict], Any],
        *,
        url_key: str = "url",
    ) -> None:
        """Run one portal/feed/marketplace row and persist the outcome."""
        self.enabled_units += 1
        name = str(row.get("name") or "")
        url = str(row.get(url_key) or "")
        log.info("%s fetch name=%s", kind, name)
        try:
            result = run_fn(row)
        except Exception as exc:
            self.record_error(name, url, str(exc))
            return
        if not _result_ok(result):
            log.info("%s error name=%s", kind, name)
            detail = str(
                getattr(result, "detail", None)
                or getattr(result, "error", None)
                or "error"
            )
            self.record_error(
                name,
                url,
                detail,
                error_class=getattr(result, "error_class", None) or None,
                http_status=getattr(result, "http_status", None),
            )
            return
        jobs = _result_jobs(result)
        log.info("%s ok name=%s jobs=%s", kind, name, len(jobs))
        _upsert_jobs(self.conn, self.profile, jobs, self.stats)
        # NOTE: an empty "ok" result still sweeps (tests/test_tombstone.py pins
        # this), so a silently broken parser can tombstone a source.
        _sweep_source(self.conn, name, jobs, self.stats, success=True)

    @property
    def failed(self) -> bool:
        return self.enabled_units > 0 and self.error_units == self.enabled_units


def _enabled_rows(payload: Any, key: str) -> list[dict]:
    return [row for row in _dict_rows(payload, key) if bool(row.get("enabled"))]


def _collect_ats(
    state: _Collector,
    rows: list,
    *,
    client: httpx.Client | None,
    sleep: Callable[[float], None],
    fetch_fn: FetchFn,
) -> tuple[httpx.Client | None, bool]:
    """Fetch every enabled ATS board. Returns (client, created_client)."""
    created = False
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        company = _row_company(raw)
        company_id = upsert_company(
            state.conn,
            company.id,
            company.name,
            company.ats,
            company.token,
            company.enabled,
        )
        ats_key = company.ats.strip().lower()
        if not company.enabled:
            log.debug("company skip id=%s yaml_enabled=false", company.id)
            continue
        if ats_key not in KNOWN_ATS:
            log.debug("company skip id=%s unknown_ats", company.id)
            continue

        state.enabled_units += 1
        if client is None:
            client = make_client()
            created = True

        log.info("company fetch id=%s ats=%s", company.id, ats_key)
        try:
            result = fetch_fn(company, client, sleep)
        except httpx.HTTPError as exc:
            result = FetchResult(FetchStatus.ERROR, None, [], error=str(exc))
        log.info(
            "company fetch done id=%s status=%s http=%s jobs=%s complete=%s",
            company.id,
            result.status.value,
            result.http_status,
            len(result.jobs),
            bool(getattr(result, "complete", True)),
        )
        state.ats_company(company, company_id, result)
    return client, created


def _collect_side_lanes(
    state: _Collector,
    *,
    skip_portals: bool,
    skip_feeds: bool,
    skip_marketplaces: bool,
    portal_fetch: tuple[str, ...] | None,
    sleep: Callable[[float], None],
    portal_run_fn: Callable[..., Any] | None,
    feed_run_fn: Callable[..., Any] | None,
    marketplace_run_fn: Callable[..., Any] | None,
    on_bundle: Callable[[Any, Any], None],
) -> None:
    from jobdigest import feeds as feeds_mod
    from jobdigest import marketplaces as marketplaces_mod
    from jobdigest import portals as portals_pkg

    portal_rows: list[dict] = []
    if not skip_portals:
        portal_rows = _enabled_rows(load_portals(), "portals")
        if portal_fetch:
            allowed = frozenset(portal_fetch)
            portal_rows = [
                row
                for row in portal_rows
                if portals_pkg.portal_fetch_mode(row) in allowed
            ]
    feed_rows = [] if skip_feeds else _enabled_rows(load_feeds(), "feeds")
    marketplace_rows = (
        [] if skip_marketplaces else _enabled_rows(load_marketplaces(), "marketplaces")
    )
    log.info(
        "portals enabled=%s feeds enabled=%s marketplaces enabled=%s",
        len(portal_rows),
        len(feed_rows),
        len(marketplace_rows),
    )

    bundle = None
    if portal_run_fn is None and portal_rows:
        modes = {
            portals_pkg.portal_fetch_mode(row)
            for row in portal_rows
            if portals_pkg.portal_fetch_mode(row) in portals_pkg.ALLOWED_FETCH
        }
        bundle = portals_pkg.open_bundle(modes)
    # Registered immediately so the caller can close it even if a lane raises.
    on_bundle(portals_pkg, bundle)

    last_host: dict = {}
    robots_cache: dict[str, str] = {}

    def run_portal(row: dict) -> Any:
        if portal_run_fn is not None:
            return portal_run_fn(row)
        return portals_pkg.run_one(
            row,
            sessions=bundle,
            sleep=sleep,
            last_host=last_host,
            robots_cache=robots_cache,
        )

    for row in portal_rows:
        state.side_unit("portal", row, run_portal)
    for row in feed_rows:
        state.side_unit("feed", row, feed_run_fn or feeds_mod.run_one)
    for row in marketplace_rows:
        state.side_unit(
            "marketplace",
            row,
            marketplace_run_fn or marketplaces_mod.run_one,
            url_key="site",
        )


def _summary_line(status: str, stats: dict[str, int]) -> str:
    return (
        f"collect {status} fetched={stats['fetched']} inserted={stats['inserted']} "
        f"updated={stats['updated']} skipped={stats['skipped']} "
        f"pass={stats['pass']} reject={stats['reject']} "
        f"missed={stats['missed']} tombstoned={stats['tombstoned']}"
    )


def run(
    *,
    db_path: Path | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] | None = None,
    fetch_fn: FetchFn | None = None,
    ats_only: bool = False,
    skip_ats: bool = False,
    skip_portals: bool = False,
    skip_feeds: bool = False,
    skip_marketplaces: bool = False,
    portal_fetch: tuple[str, ...] | None = None,
    portal_run_fn: Callable[..., Any] | None = None,
    feed_run_fn: Callable[..., Any] | None = None,
    marketplace_run_fn: Callable[..., Any] | None = None,
    telegram_http_post: TelegramHttpPost | None = None,
) -> int:
    load_dotenv()
    configure()
    sleep = sleep or time.sleep
    fetch_fn = fetch_fn or _default_fetch
    if ats_only:
        skip_portals = skip_feeds = skip_marketplaces = True

    profile = load_profile()
    payload = load_companies()
    rows = payload.get("companies") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = []
    log.info(
        "collect start ats_only=%s skip_ats=%s skip_portals=%s skip_feeds=%s "
        "skip_marketplaces=%s portal_fetch=%s yaml_enabled=%s",
        ats_only,
        skip_ats,
        skip_portals,
        skip_feeds,
        skip_marketplaces,
        portal_fetch,
        sum(1 for r in rows if isinstance(r, dict) and bool(r.get("enabled"))),
    )

    conn = connect(db_path)
    state = _Collector(conn, insert_run(conn, "collect", "running"), profile)
    own_client = False
    status = "ok"
    error_msg: str | None = None
    open_bundle: list[tuple[Any, Any]] = []
    health_footer = ""

    try:
        client, own_client = _collect_ats(
            state,
            [] if skip_ats else rows,
            client=client,
            sleep=sleep,
            fetch_fn=fetch_fn,
        )
        if not (skip_portals and skip_feeds and skip_marketplaces):
            _collect_side_lanes(
                state,
                skip_portals=skip_portals,
                skip_feeds=skip_feeds,
                skip_marketplaces=skip_marketplaces,
                portal_fetch=portal_fetch,
                sleep=sleep,
                portal_run_fn=portal_run_fn,
                feed_run_fn=feed_run_fn,
                marketplace_run_fn=marketplace_run_fn,
                on_bundle=lambda mod, bundle: open_bundle.append((mod, bundle)),
            )
        if state.failed:
            status = "error"
            error_msg = "COLLECT FAILED"
    finally:
        for mod, bundle in open_bundle:
            mod.close_bundle(bundle)
        finish_run(conn, state.run_id, status, state.stats, error=error_msg)
        try:
            text = format_health(collect_health(conn))
            if text != HEALTHY:
                health_footer = text
        except Exception:
            log.exception("health footer failed")
        conn.close()
        if own_client and client is not None:
            client.close()

    stats = state.stats
    log.info(
        "collect done status=%s %s",
        status,
        " ".join(f"{k}={v}" for k, v in stats.items()),
    )
    print(_summary_line(status, stats))
    if health_footer:
        print(health_footer)
    if status == "error":
        print("COLLECT FAILED", file=sys.stderr)
        telegram_deliver("COLLECT FAILED", required=True, http_post=telegram_http_post)
        return 1
    return 0
