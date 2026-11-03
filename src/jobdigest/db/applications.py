from __future__ import annotations

from typing import Any

from jobdigest.db._common import Conn, RowLike, norm_http_url, now
from jobdigest.db.connection import transaction

_APP_STATUSES = frozenset(
    {"applied", "oa", "interview", "offer", "rejected", "ghosted", "saved", "hidden"}
)
_BOT_STATUSES = frozenset({"saved", "hidden", "applied"})
_PROTECTED_APP = frozenset({"oa", "interview", "offer"})

_INSERT_APPLICATION = """
    INSERT INTO applications (
      job_id, apply_url_norm, company, title, applied_on, status, source, notes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def _fill_from_job(
    conn: Conn,
    norm: str,
    job_id: int | None,
    company: str,
    title: str,
    source: str,
) -> tuple[int | None, str, str, str]:
    """Fill blank fields from the jobs row sharing this normalized URL."""
    job_row = conn.execute(
        "SELECT id, company, title, source FROM jobs WHERE apply_url_norm = ?",
        (norm,),
    ).fetchone()
    if job_row is None:
        return job_id, company, title, source
    if job_id is None:
        job_id = int(job_row["id"])
    return (
        job_id,
        company or str(job_row["company"] or ""),
        title or str(job_row["title"] or ""),
        source or str(job_row["source"] or ""),
    )


def _insert_application(
    conn: Conn,
    *,
    job_id: int | None,
    norm: str,
    company: str,
    title: str,
    status: str,
    source: str,
    notes: str,
    applied_on: str,
) -> int:
    cur = conn.execute(
        _INSERT_APPLICATION,
        (job_id, norm, company, title, applied_on, status, source, notes),
    )
    return int(cur.lastrowid)


def _update_application(
    conn: Conn,
    app_id: int,
    *,
    fields: dict[str, Any],
    applied_on: str | None,
) -> None:
    """UPDATE the given columns; also reset applied_on when entering 'applied'."""
    cols = dict(fields)
    if applied_on is not None:
        cols["applied_on"] = applied_on
    assignments = ", ".join(f"{col} = ?" for col in cols)
    conn.execute(
        f"UPDATE applications SET {assignments} WHERE id = ?",
        (*cols.values(), app_id),
    )


def upsert_application(
    conn: Conn,
    *,
    apply_url: str,
    company: str = "",
    title: str = "",
    status: str = "applied",
    notes: str = "",
    source: str = "",
    job_id: int | None = None,
) -> tuple[str, int | None]:
    norm = norm_http_url(apply_url)
    if norm is None:
        return "skip", None
    st = (status or "applied").strip().lower()
    if st not in _APP_STATUSES:
        return "skip", None

    resolved_job_id, company_s, title_s, source_s = _fill_from_job(
        conn,
        norm,
        job_id,
        (company or "").strip(),
        (title or "").strip(),
        (source or "").strip(),
    )
    notes_s = notes or ""

    existing = conn.execute(
        "SELECT id, status FROM applications WHERE apply_url_norm = ?",
        (norm,),
    ).fetchone()
    with transaction(conn):
        if existing is not None:
            app_id = int(existing["id"])
            prev = str(existing["status"] or "").strip().lower()
            entering_applied = st == "applied" and prev != "applied"
            _update_application(
                conn,
                app_id,
                fields={
                    "company": company_s,
                    "title": title_s,
                    "status": st,
                    "notes": notes_s,
                    "source": source_s,
                    "job_id": resolved_job_id,
                },
                applied_on=now() if entering_applied else None,
            )
            return "update", app_id
        new_id = _insert_application(
            conn,
            job_id=resolved_job_id,
            norm=norm,
            company=company_s,
            title=title_s,
            status=st,
            source=source_s,
            notes=notes_s,
            applied_on=now(),
        )
    return "insert", new_id


def ensure_application(
    conn: Conn,
    *,
    apply_url: str,
    company: str = "",
    title: str = "",
    status: str = "saved",
    notes: str = "",
    source: str = "",
    job_id: int | None = None,
) -> tuple[str, int | None]:
    norm = norm_http_url(apply_url)
    if norm is None:
        return "skip", None
    st = (status or "saved").strip().lower()
    if st not in _APP_STATUSES:
        return "skip", None

    existing = conn.execute(
        "SELECT id FROM applications WHERE apply_url_norm = ?",
        (norm,),
    ).fetchone()
    if existing is not None:
        return "exists", int(existing["id"])

    resolved_job_id, company_s, title_s, source_s = _fill_from_job(
        conn,
        norm,
        job_id,
        (company or "").strip(),
        (title or "").strip(),
        (source or "").strip(),
    )
    with transaction(conn):
        new_id = _insert_application(
            conn,
            job_id=resolved_job_id,
            norm=norm,
            company=company_s,
            title=title_s,
            status=st,
            source=source_s,
            notes=notes or "",
            applied_on=now(),
        )
    return "insert", new_id


def pending_sheet_rows(conn: Conn) -> list[RowLike]:
    return conn.execute(
        """
        SELECT applications.*, jobs.apply_url AS job_apply_url
        FROM applications
        LEFT JOIN jobs ON jobs.id = applications.job_id
        WHERE applications.sheet_row IS NULL
        ORDER BY applications.id
        """
    ).fetchall()


def mark_sheet_synced(conn: Conn, application_id: int, sheet_row: int) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE applications SET sheet_row = ?, synced_at = ? WHERE id = ?",
            (int(sheet_row), now(), int(application_id)),
        )


def job_score_row(
    conn: Conn, job_id: int, model: str, prompt_version: str
) -> RowLike | None:
    return conn.execute(
        """
        SELECT jobs.*, scores.score AS score, scores.reasons_json AS reasons_json
        FROM jobs
        LEFT JOIN scores
          ON scores.job_id = jobs.id
         AND scores.model = ?
         AND scores.prompt_version = ?
        WHERE jobs.id = ?
        """,
        (model, prompt_version, int(job_id)),
    ).fetchone()


def apply_bot_status(conn: Conn, job_id: int, status: str) -> str:
    st = (status or "").strip().lower()
    if st not in _BOT_STATUSES:
        return "skip"
    job = conn.execute(
        """
        SELECT id, apply_url, apply_url_norm, company, title, source
        FROM jobs
        WHERE id = ?
        """,
        (int(job_id),),
    ).fetchone()
    if job is None:
        return "missing"
    norm = norm_http_url(str(job["apply_url"] or ""))
    if norm is None:
        return "skip"
    company_s = str(job["company"] or "")
    title_s = str(job["title"] or "")
    source_s = str(job["source"] or "")
    resolved_job_id = int(job["id"])
    existing = conn.execute(
        "SELECT id, status FROM applications WHERE apply_url_norm = ?",
        (norm,),
    ).fetchone()
    stamp = now()
    with transaction(conn):
        if existing is not None:
            cur = str(existing["status"] or "").strip().lower()
            if cur in _PROTECTED_APP:
                return "skip"
            entering_applied = st == "applied" and cur != "applied"
            _update_application(
                conn,
                int(existing["id"]),
                fields={
                    "company": company_s,
                    "title": title_s,
                    "status": st,
                    "source": source_s,
                    "job_id": resolved_job_id,
                },
                applied_on=stamp if entering_applied else None,
            )
            return "ok"
        _insert_application(
            conn,
            job_id=resolved_job_id,
            norm=norm,
            company=company_s,
            title=title_s,
            status=st,
            source=source_s,
            notes="",
            applied_on=stamp,
        )
    return "ok"
