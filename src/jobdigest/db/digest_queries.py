from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from jobdigest.config import Profile
from jobdigest.db._common import Conn, RowLike
from jobdigest.normalize import normalize_company
from jobdigest.rank import PRIOR_POSITIVE, sort_rows

MIN_OUTCOMES = 20
OUTCOME = frozenset({"oa", "interview", "offer", "rejected", "ghosted"})
_PRIOR_POSITIVE_STATUSES = frozenset({"oa", "interview", "offer"})


def _relevant_unapplied_select(
    conn: Conn,
    model: str,
    prompt_version: str,
) -> list[RowLike]:
    return conn.execute(
        """
        SELECT jobs.*, scores.score AS score, scores.reasons_json AS reasons_json,
               companies.display_name AS display_name
        FROM jobs
        JOIN scores ON scores.job_id = jobs.id
        LEFT JOIN companies ON companies.id = jobs.company_id
        WHERE jobs.hard_status = 'pass'
          AND jobs.is_active = 1
          AND jobs.canonical_job_id IS NULL
          AND scores.relevant = 1
          AND scores.stale = 0
          AND scores.model = ?
          AND scores.prompt_version = ?
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
        """,
        (model, prompt_version),
    ).fetchall()


def _row_notified_at(row: RowLike) -> str | None:
    try:
        value = row["notified_at"]
    except (KeyError, IndexError, TypeError):
        return None
    return value


def _company_key(name: Any) -> str:
    return normalize_company(name).casefold()


def load_company_priors(conn: Conn) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT
          applications.status AS status,
          applications.company AS app_company,
          applications.job_id AS job_id,
          jobs.company AS job_company,
          jobs.company_id AS company_id,
          companies.display_name AS display_name
        FROM applications
        LEFT JOIN jobs ON jobs.id = applications.job_id
        LEFT JOIN companies ON companies.id = jobs.company_id
        WHERE applications.status IN ('oa', 'interview', 'offer', 'rejected', 'ghosted')
        """
    ).fetchall()
    if len(rows) < MIN_OUTCOMES:
        return {}

    positive_keys: set[str] = set()
    for row in rows:
        status = str(row["status"] or "").strip().lower()
        if status not in OUTCOME:
            continue
        job_id = row["job_id"]
        job_company = str(row["job_company"] or "").strip() if job_id is not None else ""
        if job_id is not None and job_company:
            key = _company_key(job_company)
        else:
            key = _company_key(row["app_company"])
        keys: list[str] = []
        if key:
            keys.append(key)
        if row["company_id"] is not None:
            display_key = _company_key(row["display_name"])
            if display_key and display_key not in keys:
                keys.append(display_key)
        if status not in _PRIOR_POSITIVE_STATUSES:
            continue
        positive_keys.update(keys)
    return {key: float(PRIOR_POSITIVE) for key in positive_keys}


def _ranked_candidates(
    conn: Conn,
    model: str,
    prompt_version: str,
    *,
    profile: Profile,
    now: datetime | None,
    only_unnotified: bool,
) -> list[RowLike]:
    when = now or datetime.now(timezone.utc)
    rows = _relevant_unapplied_select(conn, model, prompt_version)
    if only_unnotified:
        rows = [r for r in rows if not str(_row_notified_at(r) or "").strip()]
    return sort_rows(
        rows,
        home=profile.location,
        relocate=profile.relocate,
        now=when,
        priors=load_company_priors(conn),
    )


def guest_rows(
    conn: Conn,
    model: str,
    prompt_version: str,
    digest_max: int,
    *,
    profile: Profile,
    now: datetime | None = None,
) -> list[RowLike]:
    ranked = _ranked_candidates(
        conn, model, prompt_version, profile=profile, now=now, only_unnotified=True
    )
    return ranked[: max(int(digest_max), 0)]


def relevant_unapplied_jobs(
    conn: Conn,
    model: str,
    prompt_version: str,
    *,
    profile: Profile,
    now: datetime | None = None,
) -> list[RowLike]:
    return _ranked_candidates(
        conn, model, prompt_version, profile=profile, now=now, only_unnotified=False
    )


def urgent_sent_count(conn: Conn, now: datetime) -> int:
    from jobdigest.followups import ist_date, parse_iso

    day = ist_date(now)
    rows = conn.execute(
        """
        SELECT notified_at
        FROM jobs
        WHERE notify_kind = 'urgent'
          AND notified_at IS NOT NULL
        """
    ).fetchall()
    n = 0
    for row in rows:
        dt = parse_iso(row["notified_at"])
        if dt is not None and ist_date(dt) == day:
            n += 1
    return n
