from __future__ import annotations

from jobdigest.db._common import Conn, RowLike, now
from jobdigest.db.connection import transaction
from jobdigest.db.schema import CONTACT_SOURCES
from jobdigest.normalize import collapse_ws, normalize_company


def upsert_contact(
    conn: Conn,
    *,
    job_id: int | None,
    company: str,
    name: str,
    source: str,
    email: str | None = None,
    role: str | None = None,
    domain: str | None = None,
    confidence: float | None = None,
) -> tuple[str, int | None]:
    src = (source or "").strip()
    if src not in CONTACT_SOURCES:
        return ("skip", None)
    name_s = collapse_ws(name)
    if not name_s:
        return ("skip", None)
    if job_id is None:
        return ("skip", None)
    company_s = normalize_company(company) or collapse_ws(company)
    if not company_s:
        company_s = collapse_ws(company) or "unknown"
    email_s = collapse_ws(email).lower() or None
    role_s = collapse_ws(role) or None
    domain_s = collapse_ws(domain).lower() or None
    conf = float(confidence) if confidence is not None else None
    existing = conn.execute(
        """
        SELECT id, confidence, email, role, domain FROM contacts
        WHERE job_id = ? AND name = ? AND source = ?
        """,
        (int(job_id), name_s, src),
    ).fetchone()
    with transaction(conn):
        if existing is not None:
            cid = int(existing["id"])
            old_conf = existing["confidence"]
            keep_conf = conf
            if old_conf is not None:
                try:
                    old_f = float(old_conf)
                except (TypeError, ValueError):
                    old_f = None
                else:
                    if keep_conf is None or old_f > keep_conf:
                        keep_conf = old_f
            if email_s is None:
                email_s = existing["email"]
            if role_s is None:
                role_s = existing["role"]
            if domain_s is None:
                domain_s = existing["domain"]
            conn.execute(
                """
                UPDATE contacts
                SET company = ?, domain = ?, email = ?, role = ?, confidence = ?
                WHERE id = ?
                """,
                (company_s, domain_s, email_s, role_s, keep_conf, cid),
            )
            return ("update", cid)
        cur = conn.execute(
            """
            INSERT INTO contacts (
              job_id, company, domain, name, email, role, source, verified,
              confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'unverified', ?, ?)
            """,
            (
                int(job_id),
                company_s,
                domain_s,
                name_s,
                email_s,
                role_s,
                src,
                conf,
                now(),
            ),
        )
    return ("insert", int(cur.lastrowid))


def contacts_for_job(conn: Conn, job_id: int) -> list[RowLike]:
    return conn.execute(
        """
        SELECT *
        FROM contacts
        WHERE job_id = ?
        ORDER BY
          CASE WHEN email IS NOT NULL AND email != '' THEN 0 ELSE 1 END,
          CASE source WHEN 'jd' THEN 0 WHEN 'github' THEN 1 ELSE 2 END,
          CASE WHEN confidence IS NULL THEN 1 ELSE 0 END,
          confidence DESC,
          created_at DESC,
          id DESC
        """,
        (int(job_id),),
    ).fetchall()


def best_contact_for_job(conn: Conn, job_id: int) -> RowLike | None:
    rows = contacts_for_job(conn, job_id)
    return rows[0] if rows else None
