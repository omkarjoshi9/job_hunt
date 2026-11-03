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
    github_status = "ok" if token else "skip"
    if not token:
        log.info("contacts github skip missing JOBDIGEST_GITHUB_TOKEN")
    stats = {
        "jobs": 0,
        "contacts": 0,
        "github": github_status,
        "jd_hits": 0,
        "errors": 0,
    }
    try:
        ids = list(job_ids) if job_ids is not None else last_digest_job_ids(conn)
        if limit is not None:
            ids = ids[: max(0, int(limit))]
        stats["jobs"] = len(ids)
        own_client = client is None
        http = client or make_client()
        try:
            for jid in ids:
                try:
                    row = _job_row(conn, int(jid))
                    if row is None:
                        continue
                    found = enrich_job(
                        row,
                        client=http,
                        github_token_value=token,
                    )
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
                except Exception:
                    stats["errors"] += 1
                    log.exception("contacts enrich fail job_id=%s", jid)
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
    except Exception:
        stats["errors"] += 1
        log.exception("contacts run failed")
        try:
            finish_run(conn, run_id, "error", stats, error="contacts run failed")
        except Exception:
            pass
        return 1
    finally:
        conn.close()
