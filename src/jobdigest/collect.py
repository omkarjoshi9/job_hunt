from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from jobdigest.ats import get_adapter
from jobdigest.ats.base import CompanyConfig, FetchResult, FetchStatus, NormalizedJob
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

_KNOWN_ATS = frozenset(
    {"greenhouse", "lever", "ashby", "workable", "smartrecruiters", "bamboohr"}
)

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
    if sleep is None:
        sleep = time.sleep
    if fetch_fn is None:
        fetch_fn = _default_fetch
    if ats_only:
        skip_portals = True
        skip_feeds = True
        skip_marketplaces = True

    profile = load_profile()
    payload = load_companies()
    rows = payload.get("companies") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = []
    yaml_enabled = sum(
        1 for raw in rows if isinstance(raw, dict) and bool(raw.get("enabled"))
    )
    log.info(
        "collect start ats_only=%s skip_ats=%s skip_portals=%s skip_feeds=%s "
        "skip_marketplaces=%s portal_fetch=%s yaml_enabled=%s",
        ats_only,
        skip_ats,
        skip_portals,
        skip_feeds,
        skip_marketplaces,
        portal_fetch,
        yaml_enabled,
    )

    own_client = False
    conn = connect(db_path)
    run_id = insert_run(conn, "collect", "running")
    stats = {
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
    enabled_units = 0
    error_units = 0
    status = "ok"
    error_msg: str | None = None
    bundle = None
    portals_mod = None
    health_footer = ""

    try:
        company_rows = [] if skip_ats else rows
        for raw in company_rows:
            if not isinstance(raw, dict):
                continue
            company = _row_company(raw)
            company_id = upsert_company(
                conn,
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
            if ats_key not in _KNOWN_ATS:
                log.debug("company skip id=%s unknown_ats", company.id)
                continue

            enabled_units += 1
            if client is None:
                client = make_client()
                own_client = True

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
            board_url = _board_url(company)
            streak = _dead_streak(conn, company_id, result.status)
            if result.status == FetchStatus.OK:
                update_company_fetch(
                    conn,
                    company_id,
                    http_status=result.http_status,
                    job_count=len(result.jobs),
                    error=None,
                    resolved_url=board_url,
                    dead_streak=streak,
                )
                _upsert_jobs(conn, profile, result.jobs, stats, company_id=company_id)
                _sweep_source(
                    conn,
                    company.id,
                    result.jobs,
                    stats,
                    success=bool(getattr(result, "complete", True)),
                )
            elif result.status == FetchStatus.EMPTY_BOARD:
                update_company_fetch(
                    conn,
                    company_id,
                    http_status=result.http_status,
                    job_count=0,
                    error=None,
                    resolved_url=board_url,
                    dead_streak=streak,
                )
                _sweep_source(
                    conn,
                    company.id,
                    [],
                    stats,
                    success=bool(getattr(result, "complete", True)),
                )
            elif result.status == FetchStatus.DEAD_TOKEN:
                update_company_fetch(
                    conn,
                    company_id,
                    http_status=result.http_status,
                    job_count=0,
                    error="dead_token",
                    resolved_url=board_url,
                    dead_streak=streak,
                )
            else:
                error_units += 1
                stats["errors"] += 1
                detail = result.error or "error"
                update_company_fetch(
                    conn,
                    company_id,
                    http_status=result.http_status,
                    job_count=0,
                    error=detail,
                    resolved_url=board_url,
                    dead_streak=streak,
                )
                insert_portal_error(
                    conn,
                    run_id=run_id,
                    portal_name=company.id,
                    url=_board_url(company),
                    error_class=_error_class(detail),
                    http_status=result.http_status,
                    detail=detail,
                )

        if not skip_portals or not skip_feeds or not skip_marketplaces:
            from jobdigest import feeds as feeds_mod
            from jobdigest import marketplaces as marketplaces_mod
            from jobdigest import portals as portals_pkg

            portals_mod = portals_pkg
            portal_rows = []
            if not skip_portals:
                portal_rows = [
                    row
                    for row in _dict_rows(load_portals(), "portals")
                    if bool(row.get("enabled"))
                ]
                if portal_fetch:
                    allowed = frozenset(portal_fetch)
                    portal_rows = [
                        row
                        for row in portal_rows
                        if portals_pkg.portal_fetch_mode(row) in allowed
                    ]
            feed_rows = []
            if not skip_feeds:
                feed_rows = [
                    row
                    for row in _dict_rows(load_feeds(), "feeds")
                    if bool(row.get("enabled"))
                ]
            marketplace_rows = []
            if not skip_marketplaces:
                marketplace_rows = [
                    row
                    for row in _dict_rows(load_marketplaces(), "marketplaces")
                    if bool(row.get("enabled"))
                ]
            log.info(
                "portals enabled=%s feeds enabled=%s marketplaces enabled=%s",
                len(portal_rows),
                len(feed_rows),
                len(marketplace_rows),
            )
            last_host: dict = {}
            robots_cache: dict[str, str] = {}
            if portal_run_fn is None and portal_rows:
                modes = {
                    portals_pkg.portal_fetch_mode(row)
                    for row in portal_rows
                    if portals_pkg.portal_fetch_mode(row) in portals_pkg.ALLOWED_FETCH
                }
                bundle = portals_pkg.open_bundle(modes)

            for row in portal_rows:
                enabled_units += 1
                name = str(row.get("name") or "")
                url = str(row.get("url") or "")
                log.info("portal fetch name=%s", name)
                try:
                    if portal_run_fn is not None:
                        result = portal_run_fn(row)
                    else:
                        result = portals_pkg.run_one(
                            row,
                            sessions=bundle,
                            sleep=sleep,
                            last_host=last_host,
                            robots_cache=robots_cache,
                        )
                except Exception as exc:
                    error_units += 1
                    stats["errors"] += 1
                    insert_portal_error(
                        conn,
                        run_id=run_id,
                        portal_name=name,
                        url=url,
                        error_class=_error_class(str(exc)),
                        http_status=None,
                        detail=str(exc),
                    )
                    continue
                if _result_ok(result):
                    portal_jobs = _result_jobs(result)
                    log.info("portal ok name=%s jobs=%s", name, len(portal_jobs))
                    _upsert_jobs(conn, profile, portal_jobs, stats)
                    _sweep_source(conn, name, portal_jobs, stats, success=True)
                else:
                    error_units += 1
                    stats["errors"] += 1
                    log.info("portal error name=%s", name)
                    detail = str(getattr(result, "detail", None) or getattr(result, "error", None) or "error")
                    insert_portal_error(
                        conn,
                        run_id=run_id,
                        portal_name=name,
                        url=url,
                        error_class=str(getattr(result, "error_class", None) or _error_class(detail)),
                        http_status=getattr(result, "http_status", None),
                        detail=detail,
                    )

            for row in feed_rows:
                enabled_units += 1
                name = str(row.get("name") or "")
                url = str(row.get("url") or "")
                log.info("feed fetch name=%s", name)
                try:
                    if feed_run_fn is not None:
                        result = feed_run_fn(row)
                    else:
                        result = feeds_mod.run_one(row)
                except Exception as exc:
                    error_units += 1
                    stats["errors"] += 1
                    insert_portal_error(
                        conn,
                        run_id=run_id,
                        portal_name=name,
                        url=url,
                        error_class=_error_class(str(exc)),
                        http_status=None,
                        detail=str(exc),
                    )
                    continue
                if _result_ok(result):
                    feed_jobs = _result_jobs(result)
                    log.info("feed ok name=%s jobs=%s", name, len(feed_jobs))
                    _upsert_jobs(conn, profile, feed_jobs, stats)
                    _sweep_source(conn, name, feed_jobs, stats, success=True)
                else:
                    error_units += 1
                    stats["errors"] += 1
                    log.info("feed error name=%s", name)
                    detail = str(getattr(result, "detail", None) or getattr(result, "error", None) or "error")
                    insert_portal_error(
                        conn,
                        run_id=run_id,
                        portal_name=name,
                        url=url,
                        error_class=str(getattr(result, "error_class", None) or "http"),
                        http_status=getattr(result, "http_status", None),
                        detail=detail,
                    )

            for row in marketplace_rows:
                enabled_units += 1
                name = str(row.get("name") or "")
                site = str(row.get("site") or "")
                log.info("marketplace fetch name=%s site=%s", name, site)
                try:
                    if marketplace_run_fn is not None:
                        result = marketplace_run_fn(row)
                    else:
                        result = marketplaces_mod.run_one(row)
                except Exception as exc:
                    error_units += 1
                    stats["errors"] += 1
                    insert_portal_error(
                        conn,
                        run_id=run_id,
                        portal_name=name,
                        url=site,
                        error_class=_error_class(str(exc)),
                        http_status=None,
                        detail=str(exc),
                    )
                    continue
                if _result_ok(result):
                    marketplace_jobs = _result_jobs(result)
                    log.info("marketplace ok name=%s jobs=%s", name, len(marketplace_jobs))
                    _upsert_jobs(conn, profile, marketplace_jobs, stats)
                    _sweep_source(conn, name, marketplace_jobs, stats, success=True)
                else:
                    error_units += 1
                    stats["errors"] += 1
                    log.info("marketplace error name=%s", name)
                    detail = str(getattr(result, "detail", None) or getattr(result, "error", None) or "error")
                    insert_portal_error(
                        conn,
                        run_id=run_id,
                        portal_name=name,
                        url=site,
                        error_class=str(getattr(result, "error_class", None) or _error_class(detail)),
                        http_status=getattr(result, "http_status", None),
                        detail=detail,
                    )

        if enabled_units > 0 and error_units == enabled_units:
            status = "error"
            error_msg = "COLLECT FAILED"
    finally:
        if portals_mod is not None:
            portals_mod.close_bundle(bundle)
        finish_run(conn, run_id, status, stats, error=error_msg)
        try:
            text = format_health(collect_health(conn))
            if text != HEALTHY:
                health_footer = text
        except Exception:
            log.exception("health footer failed")
        conn.close()
        if own_client and client is not None:
            client.close()

    line = (
        f"collect {status} fetched={stats['fetched']} inserted={stats['inserted']} "
        f"updated={stats['updated']} skipped={stats['skipped']} "
        f"pass={stats['pass']} reject={stats['reject']} "
        f"missed={stats['missed']} tombstoned={stats['tombstoned']}"
    )
    log.info(
        "collect done status=%s fetched=%s inserted=%s updated=%s skipped=%s "
        "pass=%s reject=%s errors=%s missed=%s tombstoned=%s",
        status,
        stats["fetched"],
        stats["inserted"],
        stats["updated"],
        stats["skipped"],
        stats["pass"],
        stats["reject"],
        stats["errors"],
        stats["missed"],
        stats["tombstoned"],
    )
    print(line)
    if health_footer:
        print(health_footer)
    if status == "error":
        print("COLLECT FAILED", file=sys.stderr)
        telegram_deliver(
            "COLLECT FAILED", required=True, http_post=telegram_http_post
        )
        return 1
    return 0
