from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from jobdigest.ats import get_adapter
from jobdigest.ats.base import CompanyConfig, FetchResult, FetchStatus, NormalizedJob
from jobdigest.ats.http_client import make_client
from jobdigest.config import load_companies, load_feeds, load_portals, load_profile
from jobdigest.db import (
    connect,
    finish_run,
    insert_portal_error,
    insert_run,
    job_hard_status,
    update_company_fetch,
    upsert_company,
    upsert_job,
)
from jobdigest.envfile import load_dotenv
from jobdigest.logutil import configure, get_logger
from jobdigest.telegram import deliver as telegram_deliver

log = get_logger("collect")

_KNOWN_ATS = frozenset({"greenhouse", "lever", "ashby", "workable"})

FetchFn = Callable[
    [CompanyConfig, httpx.Client | None, Callable[[float], None]],
    FetchResult,
]
TelegramHttpPost = Callable[..., object]


def enable_overrides() -> set[str]:
    raw = os.environ.get("JOBDIGEST_ENABLE") or ""
    return {part.strip() for part in raw.split(",") if part.strip()}


def _company_enabled(company: CompanyConfig, overrides: set[str]) -> bool:
    if company.enabled:
        return True
    return company.id in overrides or f"company:{company.id}" in overrides


def _row_enabled(row: dict, *, kind: str, overrides: set[str]) -> bool:
    if row.get("enabled"):
        return True
    ident = str(row.get("id") or row.get("name") or "")
    if not ident:
        return False
    return f"{kind}:{ident}" in overrides


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
    return f"ats://{company.ats}/{token}"


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
    portal_run_fn: Callable[..., Any] | None = None,
    feed_run_fn: Callable[..., Any] | None = None,
    telegram_http_post: TelegramHttpPost | None = None,
) -> int:
    load_dotenv()
    configure()
    if sleep is None:
        sleep = time.sleep
    if fetch_fn is None:
        fetch_fn = _default_fetch

    overrides = enable_overrides()
    log.info(
        "collect start ats_only=%s enable=%s",
        ats_only,
        ",".join(sorted(overrides)) or "(none)",
    )
    profile = load_profile()
    payload = load_companies()
    rows = payload.get("companies") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = []

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
    }
    enabled_units = 0
    error_units = 0
    status = "ok"
    error_msg: str | None = None
    session = None
    portals_mod = None

    try:
        for raw in rows:
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
            if not _company_enabled(company, overrides):
                log.debug(
                    "company skip id=%s yaml_enabled=%s",
                    company.id,
                    company.enabled,
                )
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
                "company fetch done id=%s status=%s http=%s jobs=%s",
                company.id,
                result.status.value,
                result.http_status,
                len(result.jobs),
            )
            if result.status == FetchStatus.OK:
                update_company_fetch(
                    conn,
                    company_id,
                    http_status=result.http_status,
                    job_count=len(result.jobs),
                    error=None,
                )
                _upsert_jobs(conn, profile, result.jobs, stats, company_id=company_id)
            elif result.status == FetchStatus.EMPTY_BOARD:
                update_company_fetch(
                    conn,
                    company_id,
                    http_status=result.http_status,
                    job_count=0,
                    error=None,
                )
            elif result.status == FetchStatus.DEAD_TOKEN:
                update_company_fetch(
                    conn,
                    company_id,
                    http_status=result.http_status,
                    job_count=0,
                    error="dead_token",
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

        if not ats_only:
            from jobdigest import feeds as feeds_mod
            from jobdigest import portals as portals_pkg

            portals_mod = portals_pkg
            portal_rows = [
                row
                for row in _dict_rows(load_portals(), "portals")
                if _row_enabled(row, kind="portal", overrides=overrides)
            ]
            feed_rows = [
                row
                for row in _dict_rows(load_feeds(), "feeds")
                if _row_enabled(row, kind="feed", overrides=overrides)
            ]
            log.info("portals enabled=%s feeds enabled=%s", len(portal_rows), len(feed_rows))
            last_host: dict = {}
            robots_cache: dict[str, str] = {}
            session_error: str | None = None
            if portal_run_fn is None and portal_rows:
                try:
                    session = portals_pkg.open_session()
                except Exception as exc:
                    session_error = str(exc)

            for row in portal_rows:
                enabled_units += 1
                name = str(row.get("name") or "")
                url = str(row.get("url") or "")
                log.info("portal fetch name=%s", name)
                try:
                    if portal_run_fn is not None:
                        result = portal_run_fn(row)
                    elif session_error:
                        result = portals_pkg.PortalResult(
                            status="error",
                            error_class="http",
                            detail=session_error,
                            name=name,
                            url=url,
                        )
                    else:
                        result = portals_pkg.run_one(
                            row,
                            session=session,
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
                    log.info("portal ok name=%s jobs=%s", name, len(_result_jobs(result)))
                    _upsert_jobs(conn, profile, _result_jobs(result), stats)
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
                    log.info("feed ok name=%s jobs=%s", name, len(_result_jobs(result)))
                    _upsert_jobs(conn, profile, _result_jobs(result), stats)
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

        if enabled_units > 0 and error_units == enabled_units:
            status = "error"
            error_msg = "COLLECT FAILED"
    finally:
        if portals_mod is not None:
            portals_mod.close_session(session)
        finish_run(conn, run_id, status, stats, error=error_msg)
        conn.close()
        if own_client and client is not None:
            client.close()

    line = (
        f"collect {status} fetched={stats['fetched']} inserted={stats['inserted']} "
        f"updated={stats['updated']} skipped={stats['skipped']} "
        f"pass={stats['pass']} reject={stats['reject']}"
    )
    log.info(
        "collect done status=%s fetched=%s inserted=%s updated=%s skipped=%s pass=%s reject=%s errors=%s",
        status,
        stats["fetched"],
        stats["inserted"],
        stats["updated"],
        stats["skipped"],
        stats["pass"],
        stats["reject"],
        stats["errors"],
    )
    print(line)
    if status == "error":
        print("COLLECT FAILED", file=sys.stderr)
        telegram_deliver(
            "COLLECT FAILED", required=True, http_post=telegram_http_post
        )
        return 1
    return 0
