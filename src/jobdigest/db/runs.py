from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from jobdigest.db._common import Conn, RowLike, now
from jobdigest.db.connection import transaction


def insert_run(conn: Conn, kind: str, status: str = "running") -> int:
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO runs (kind, started_at, status) VALUES (?, ?, ?)",
            (kind, now(), status),
        )
    return int(cur.lastrowid)


def finish_run(
    conn: Conn,
    run_id: int,
    status: str,
    stats: dict[str, Any],
    error: str | None = None,
) -> None:
    with transaction(conn):
        conn.execute(
            """
            UPDATE runs
            SET finished_at = ?, status = ?, stats_json = ?, error = ?
            WHERE id = ?
            """,
            (now(), status, json.dumps(stats, separators=(",", ":")), error, run_id),
        )


def insert_portal_error(
    conn: Conn,
    *,
    run_id: int,
    portal_name: str,
    url: str,
    error_class: str,
    http_status: int | None,
    detail: str | None,
) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO portal_errors (
              run_id, portal_name, url, error_class, http_status, detail, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, portal_name, url, error_class, http_status, detail, now()),
        )


def _parse_iso(ts: str) -> datetime:
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def last_collect_run(conn: Conn) -> RowLike | None:
    return conn.execute(
        "SELECT * FROM runs WHERE kind = 'collect' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def collect_is_ok(conn: Conn, *, max_age_hours: float = 26) -> bool:
    row = last_collect_run(conn)
    if row is None:
        return False
    if str(row["status"]) != "ok":
        return False
    finished = row["finished_at"]
    if not finished:
        return False
    try:
        dt = _parse_iso(str(finished))
    except (ValueError, TypeError):
        return False
    age = datetime.now(timezone.utc) - dt
    return age.total_seconds() <= max_age_hours * 3600


def last_ok_run(conn: Conn, kind: str) -> RowLike | None:
    return conn.execute(
        """
        SELECT *
        FROM runs
        WHERE kind = ? AND status = 'ok'
        ORDER BY id DESC
        LIMIT 1
        """,
        (kind,),
    ).fetchone()


def last_digest_job_ids(conn: Conn) -> list[int]:
    row = last_ok_run(conn, "digest")
    if row is None:
        return []
    raw = row["stats_json"]
    if not raw:
        return []
    try:
        stats = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(stats, dict):
        return []
    ids = stats.get("job_ids")
    if not isinstance(ids, list):
        return []
    out: list[int] = []
    for item in ids:
        try:
            jid = int(item)
        except (TypeError, ValueError):
            continue
        if jid > 0:
            out.append(jid)
    return out


def unscored_pass_jobs(
    conn: Conn, model: str, prompt_version: str
) -> list[RowLike]:
    return conn.execute(
        """
        SELECT jobs.*
        FROM jobs
        WHERE jobs.hard_status = 'pass'
          AND jobs.is_active = 1
          AND jobs.canonical_job_id IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM scores
            WHERE scores.job_id = jobs.id
              AND scores.model = ?
              AND scores.prompt_version = ?
              AND scores.stale = 0
          )
          AND NOT EXISTS (
            SELECT 1 FROM applications
            WHERE applications.apply_url_norm = jobs.apply_url_norm
          )
          AND NOT EXISTS (
            SELECT 1 FROM applications a
            JOIN jobs j2 ON j2.apply_url_norm = a.apply_url_norm
            WHERE jobs.posting_key IS NOT NULL
              AND jobs.posting_key != ''
              AND j2.posting_key = jobs.posting_key
          )
        ORDER BY jobs.id ASC
        """,
        (model, prompt_version),
    ).fetchall()


def upsert_score(
    conn: Conn,
    job_id: int,
    model: str,
    prompt_version: str,
    relevant: bool,
    score: float,
    reasons: dict,
) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO scores (
              job_id, model, prompt_version, relevant, score, reasons_json, created_at, stale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(job_id, model, prompt_version) DO UPDATE SET
              relevant = excluded.relevant,
              score = excluded.score,
              reasons_json = excluded.reasons_json,
              created_at = excluded.created_at,
              stale = 0
            """,
            (
                job_id,
                model,
                prompt_version,
                1 if relevant else 0,
                float(score),
                json.dumps(reasons, separators=(",", ":")),
                now(),
            ),
        )
