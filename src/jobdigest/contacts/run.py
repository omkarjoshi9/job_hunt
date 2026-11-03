from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from jobdigest.ats.http_client import make_client
from jobdigest.contacts.pipeline import enrich_job
from jobdigest.envfile import load_dotenv
from jobdigest.logutil import configure, get_logger
from jobdigest.db import (
    connect,
    finish_run,
    insert_run,
    last_digest_job_ids,
    upsert_contact,
)

log = get_logger("contacts")


def _job_row(conn: Any, job_id: int) -> Any:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()


def _resolve_ids(
    conn: Any, job_ids: list[int] | None, limit: int | None
) -> list[int]:
    ids = list(job_ids) if job_ids is not None else last_digest_job_ids(conn)
    if limit is not None:
        ids = ids[: max(0, int(limit))]
    return ids


def _enrich_one(
    conn: Any, jid: int, http: httpx.Client, token: str, stats: dict[str, Any]
) -> None:
    row = _job_row(conn, int(jid))
    if row is None:
        return
    found = enrich_job(row, client=http, github_token_value=token)
    for person in found:
        if person.source == "jd":
            stats["jd_hits"] += 1
        action, _cid = upsert_contact(
            conn,
            job_id=int(jid),
            company=person.company or str(row["company"] or ""),
            name=person.name,
            source=person.source,
            email=person.email,
            role=person.role,
            domain=person.domain,
            confidence=person.confidence,
        )
        if action in ("insert", "update"):
            stats["contacts"] += 1


def _enrich_all(
    conn: Any, ids: list[int], http: httpx.Client, token: str, stats: dict[str, Any]
) -> None:
    for jid in ids:
        try:
            _enrich_one(conn, jid, http, token, stats)
        except Exception:  # noqa: BLE001 - per-job isolation; logged with traceback
            stats["errors"] += 1
            log.exception("contacts enrich fail job_id=%s", jid)


def _record_failure(conn: Any, run_id: int, stats: dict[str, Any]) -> None:
    try:
        finish_run(conn, run_id, "error", stats, error="contacts run failed")
    except Exception:  # noqa: BLE001 - already failing; do not mask the original error
        log.exception("contacts finish_run(error) failed run_id=%s", run_id)


def run(
    *,
    db_path: Path | None = None,
    limit: int | None = None,
    job_ids: list[int] | None = None,
    client: httpx.Client | None = None,
) -> int:
    load_dotenv()
    configure()
    conn = connect(db_path)
    run_id = insert_run(conn, "contacts")
    token = (os.environ.get("JOBDIGEST_GITHUB_TOKEN") or "").strip()
    if not token:
        log.info("contacts github skip missing JOBDIGEST_GITHUB_TOKEN")
    stats: dict[str, Any] = {
        "jobs": 0,
        "contacts": 0,
        "github": "ok" if token else "skip",
        "jd_hits": 0,
        "errors": 0,
    }
    try:
        ids = _resolve_ids(conn, job_ids, limit)
        stats["jobs"] = len(ids)
        own_client = client is None
        http = client or make_client()
        try:
            _enrich_all(conn, ids, http, token, stats)
        finally:
            if own_client:
                http.close()
        finish_run(conn, run_id, "ok", stats)
        log.info(
            "contacts done jobs=%s contacts=%s github=%s jd_hits=%s errors=%s",
            stats["jobs"],
            stats["contacts"],
            stats["github"],
            stats["jd_hits"],
            stats["errors"],
        )
        return 0
    except Exception:  # noqa: BLE001 - top-level; logged with traceback, exit 1
        stats["errors"] += 1
        log.exception("contacts run failed")
        _record_failure(conn, run_id, stats)
        return 1
    finally:
        conn.close()
