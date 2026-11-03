from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from jobdigest.ats.base import KNOWN_ATS
from jobdigest.config import Profile
from jobdigest.db._common import Conn, now, row_get
from jobdigest.db.connection import transaction
from jobdigest.hard_filter import hard_filter
from jobdigest.legitimacy import evaluate_structural
from jobdigest.normalize import (
    collapse_ws,
    description_hash,
    job_identity,
    normalize_company,
    normalize_title,
    posting_key,
)

_HINT_MAX = 80


# --- companies -------------------------------------------------------------


def upsert_company(
    conn: Conn,
    slug: str,
    display_name: str,
    ats: str,
    board_token: str,
    enabled: bool,
) -> int:
    row = conn.execute("SELECT id FROM companies WHERE slug = ?", (slug,)).fetchone()
    flag = 1 if enabled else 0
    with transaction(conn):
        if row:
            conn.execute(
                """
                UPDATE companies
                SET display_name = ?, ats = ?, board_token = ?, enabled = ?
                WHERE id = ?
                """,
                (display_name, ats, board_token, flag, int(row["id"])),
            )
            return int(row["id"])
        cur = conn.execute(
            """
            INSERT INTO companies (slug, display_name, ats, board_token, enabled)
            VALUES (?, ?, ?, ?, ?)
            """,
            (slug, display_name, ats, board_token, flag),
        )
    return int(cur.lastrowid)


def update_company_fetch(
    conn: Conn,
    company_id: int,
    *,
    http_status: int | None,
    job_count: int | None,
    error: str | None,
    resolved_url: str | None = None,
    dead_streak: int | None = None,
) -> None:
    sets = [
        "last_fetch_at = ?",
        "last_http_status = ?",
        "last_job_count = ?",
        "last_error = ?",
    ]
    params: list[Any] = [now(), http_status, job_count, error]
    if resolved_url is not None:
        sets.append("resolved_url = ?")
        params.append(resolved_url)
    if dead_streak is not None:
        sets.append("dead_streak = ?")
        params.append(int(dead_streak))
    params.append(int(company_id))
    with transaction(conn):
        conn.execute(
            f"UPDATE companies SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )


# --- helpers ---------------------------------------------------------------


def _sanitize_hint(value: str | None, *, max_len: int = _HINT_MAX) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text[:max_len]


def _store_posted_at(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc).replace(microsecond=0)
        return dt.isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text or None


def lookup_job(
    conn: Conn,
    apply_url_norm: str,
    source: str,
    external_id: str,
) -> int | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE apply_url_norm = ?",
        (apply_url_norm,),
    ).fetchone()
    if row:
        return int(row["id"])
    ext = (external_id or "").strip()
    if ext:
        row = conn.execute(
            "SELECT id FROM jobs WHERE source = ? AND external_id = ?",
            (source, ext),
        ).fetchone()
        if row:
            return int(row["id"])
    return None


def _ats_rank(ats: str | None) -> int:
    return 0 if (ats or "").strip().lower() in KNOWN_ATS else 1


def link_duplicate_jobs(conn: Conn, job_id: int) -> None:
    row = conn.execute(
        "SELECT posting_key FROM jobs WHERE id = ?",
        (int(job_id),),
    ).fetchone()
    if row is None:
        return
    key = str(row_get(row, "posting_key", "") or "").strip()
    if not key:
        return
    rows = conn.execute(
        """
        SELECT id, ats, notified_at, notify_kind
        FROM jobs
        WHERE posting_key = ?
        """,
        (key,),
    ).fetchall()
    if len(rows) < 2:
        conn.execute(
            "UPDATE jobs SET canonical_job_id = NULL WHERE id = ?",
            (int(job_id),),
        )
        return
    ranked = sorted(
        rows,
        key=lambda r: (_ats_rank(r["ats"]), int(r["id"])),
    )
    winner_id = int(ranked[0]["id"])
    notify_at = ranked[0]["notified_at"]
    notify_kind = ranked[0]["notify_kind"]
    if not str(notify_at or "").strip():
        for item in rows:
            if str(item["notified_at"] or "").strip():
                notify_at = item["notified_at"]
                notify_kind = item["notify_kind"]
                break
    conn.execute(
        """
        UPDATE jobs
        SET canonical_job_id = NULL, notified_at = ?, notify_kind = ?
        WHERE id = ?
        """,
        (notify_at, notify_kind, winner_id),
    )
    for item in rows:
        rid = int(item["id"])
        if rid == winner_id:
            continue
        conn.execute(
            "UPDATE jobs SET canonical_job_id = ? WHERE id = ?",
            (winner_id, rid),
        )


def mark_scores_stale(conn: Conn, job_id: int) -> None:
    conn.execute("UPDATE scores SET stale = 1 WHERE job_id = ?", (int(job_id),))


def _company_display_name(conn: Conn, company_id: int | None) -> str | None:
    if not company_id:
        return None
    row = conn.execute(
        "SELECT display_name FROM companies WHERE id = ?",
        (int(company_id),),
    ).fetchone()
    if row is None:
        return None
    text = str(row_get(row, "display_name") or "").strip()
    return text or None


def _legitimacy_peers(conn: Conn, job_id: int, desc_hash: str) -> list[dict[str, Any]]:
    if not desc_hash:
        return []
    rows = conn.execute(
        """
        SELECT id, description_hash, source, external_id, apply_url_norm,
               canonical_job_id, description_text
        FROM jobs
        WHERE description_hash = ? AND id != ?
        """,
        (desc_hash, int(job_id)),
    ).fetchall()
    peers: list[dict[str, Any]] = []
    for row in rows:
        text = str(row["description_text"] or "")
        peers.append(
            {
                "id": row["id"],
                "description_hash": row["description_hash"],
                "source": row["source"],
                "external_id": row["external_id"],
                "apply_url_norm": row["apply_url_norm"],
                "canonical_job_id": row["canonical_job_id"],
                "description_len": len(collapse_ws(text)),
            }
        )
    return peers


def _stamp_legitimacy(conn: Conn, job_id: int) -> None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
    if row is None:
        return
    company_id = row_get(row, "company_id")
    desc_hash = str(row["description_hash"] or "")
    job = {
        "company_id": company_id,
        "raw_company": row["raw_company"],
        "company": row["company"],
        "description_text": row["description_text"],
        "description_hash": desc_hash,
        "source": row["source"],
        "external_id": row["external_id"],
        "apply_url_norm": row["apply_url_norm"],
        "canonical_job_id": row["canonical_job_id"],
        "hard_status": row["hard_status"],
        "title": row["title"],
        "location_raw": row["location_raw"],
    }
    flag, codes = evaluate_structural(
        job,
        company_display_name=_company_display_name(conn, company_id),
        peers=_legitimacy_peers(conn, job_id, desc_hash),
    )
    conn.execute(
        """
        UPDATE jobs
        SET legitimacy_flag = ?, legitimacy_reasons_json = ?
        WHERE id = ?
        """,
        (flag, json.dumps(codes, separators=(",", ":")), int(job_id)),
    )


# --- upsert_job ------------------------------------------------------------


@dataclass(frozen=True)
class _JobRow:
    """Normalized column values for one job, ready to insert or update."""

    apply_url: str | None
    apply_url_norm: str
    source: str
    external_id: str | None
    company_id: int | None
    company: str
    title: str
    location_raw: str
    description_text: str
    raw_json: str
    hard_status: str
    hard_reason: str | None
    ats: str
    employment_hint: str | None
    workplace_hint: str | None
    description_hash: str
    raw_title: str
    raw_company: str
    posting_key: str
    posted_at: str | None


# Columns written identically by INSERT and UPDATE, in a fixed order.
_SHARED_COLUMNS = (
    "apply_url",
    "title",
    "location_raw",
    "description_text",
    "raw_json",
    "hard_status",
    "hard_reason",
    "company",
    "company_id",
    "ats",
    "external_id",
    "employment_hint",
    "workplace_hint",
    "description_hash",
    "raw_title",
    "raw_company",
    "posting_key",
    "posted_at",
)
_INSERT_ONLY_COLUMNS = ("apply_url_norm", "source")


def _build_job_row(
    profile: Profile,
    *,
    col_url: str | None,
    norm: str,
    source: str,
    external_id: str,
    company_id: int | None,
    company: str,
    title: str,
    location_raw: str,
    description_text: str,
    raw_json: str,
    ats: str,
    employment_hint: str | None,
    workplace_hint: str | None,
    posted_at: datetime | str | None,
) -> _JobRow:
    employment_hint = _sanitize_hint(employment_hint)
    workplace_hint = _sanitize_hint(workplace_hint)
    raw_title = (title or "").strip() or "(untitled)"
    raw_company = (company or "").strip()
    status, reason = hard_filter(
        profile,
        title=raw_title,
        location_raw=location_raw,
        description_text=description_text,
        employment_hint=employment_hint,
        company=raw_company,
    )
    job_title = normalize_title(raw_title) or "(untitled)"
    job_company = normalize_company(raw_company) if raw_company else raw_company
    return _JobRow(
        apply_url=col_url,
        apply_url_norm=norm,
        source=source,
        external_id=(external_id or "").strip() or None,
        company_id=company_id,
        company=job_company,
        title=job_title,
        location_raw=location_raw,
        description_text=description_text,
        raw_json=raw_json,
        hard_status=status,
        hard_reason=reason,
        ats=ats,
        employment_hint=employment_hint,
        workplace_hint=workplace_hint,
        description_hash=description_hash(description_text),
        raw_title=raw_title,
        raw_company=raw_company,
        posting_key=posting_key(job_title, job_company, location_raw or ""),
        posted_at=_store_posted_at(posted_at),
    )


def _stored_description_hash(conn: Conn, job_id: int) -> str:
    prev = conn.execute(
        "SELECT description_hash FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if prev is None:
        return ""
    return str(row_get(prev, "description_hash", "") or "")


def _update_job_row(conn: Conn, job_id: int, row: _JobRow, seen_at: str) -> None:
    assignments = ", ".join(f"{col} = ?" for col in _SHARED_COLUMNS)
    conn.execute(
        f"""
        UPDATE jobs
        SET {assignments},
            last_seen_at = ?,
            is_active = 1,
            miss_count = 0,
            closed_at = NULL,
            close_reason = NULL
        WHERE id = ?
        """,
        (*(getattr(row, col) for col in _SHARED_COLUMNS), seen_at, job_id),
    )


def _insert_job_row(conn: Conn, row: _JobRow, seen_at: str) -> int:
    cols = _INSERT_ONLY_COLUMNS + _SHARED_COLUMNS
    names = ", ".join(cols + ("first_seen_at", "last_seen_at", "is_active", "miss_count"))
    marks = ", ".join("?" for _ in cols) + ", ?, ?, 1, 0"
    cur = conn.execute(
        f"INSERT INTO jobs ({names}) VALUES ({marks})",
        (*(getattr(row, col) for col in cols), seen_at, seen_at),
    )
    return int(cur.lastrowid)


def _post_process_job(conn: Conn, job_id: int) -> None:
    link_duplicate_jobs(conn, job_id)
    _stamp_legitimacy(conn, job_id)


def upsert_job(
    conn: Conn,
    profile: Profile,
    *,
    apply_url: str,
    hosted_url: str,
    source: str,
    external_id: str,
    company_id: int | None,
    company: str,
    title: str,
    location_raw: str,
    description_text: str,
    raw_json: str,
    ats: str,
    employment_hint: str | None = None,
    workplace_hint: str | None = None,
    posted_at: datetime | str | None = None,
) -> tuple[str, int | None]:
    col_url, norm = job_identity(apply_url, hosted_url, source, external_id)
    if norm is None:
        return "skip", None
    row = _build_job_row(
        profile,
        col_url=col_url,
        norm=norm,
        source=source,
        external_id=external_id,
        company_id=company_id,
        company=company,
        title=title,
        location_raw=location_raw,
        description_text=description_text,
        raw_json=raw_json,
        ats=ats,
        employment_hint=employment_hint,
        workplace_hint=workplace_hint,
        posted_at=posted_at,
    )
    seen_at = now()
    with transaction(conn):
        existing = lookup_job(conn, norm, source, external_id)
        if existing is None:
            new_id = _insert_job_row(conn, row, seen_at)
            _post_process_job(conn, new_id)
            return "insert", new_id
        old_hash = _stored_description_hash(conn, existing)
        _update_job_row(conn, existing, row, seen_at)
        if old_hash and old_hash != row.description_hash:
            mark_scores_stale(conn, existing)
        _post_process_job(conn, existing)
        return "update", existing


# --- seen / tombstone ------------------------------------------------------


def job_seen_key(*, external_id: str | None, apply_url_norm: str | None) -> str | None:
    ext = (external_id or "").strip()
    if ext:
        return ext
    norm = (apply_url_norm or "").strip()
    return norm or None


def note_seen_job(conn: Conn, job_id: int) -> None:
    with transaction(conn):
        conn.execute(
            """
            UPDATE jobs
            SET is_active = 1,
                miss_count = 0,
                closed_at = NULL,
                close_reason = NULL
            WHERE id = ?
            """,
            (int(job_id),),
        )


def bump_unseen_for_source(
    conn: Conn,
    source: str,
    seen_keys: set[str] | frozenset[str],
    *,
    success: bool,
) -> dict[str, int]:
    if not success:
        return {"missed": 0, "tombstoned": 0}
    source_s = (source or "").strip()
    if not source_s:
        return {"missed": 0, "tombstoned": 0}
    rows = conn.execute(
        """
        SELECT id, external_id, apply_url_norm, miss_count
        FROM jobs
        WHERE source = ? AND is_active = 1
        """,
        (source_s,),
    ).fetchall()
    missed = 0
    tombstoned = 0
    stamp = now()
    seen = set(seen_keys)
    with transaction(conn):
        for row in rows:
            key = job_seen_key(
                external_id=row["external_id"],
                apply_url_norm=row["apply_url_norm"],
            )
            if key is None or key in seen:
                continue
            missed += 1
            new_miss = int(row["miss_count"] or 0) + 1
            if new_miss >= 2:
                conn.execute(
                    """
                    UPDATE jobs
                    SET miss_count = ?, is_active = 0, closed_at = ?, close_reason = ?
                    WHERE id = ?
                    """,
                    (new_miss, stamp, "unseen_board", int(row["id"])),
                )
                tombstoned += 1
            else:
                conn.execute(
                    "UPDATE jobs SET miss_count = ? WHERE id = ?",
                    (new_miss, int(row["id"])),
                )
    return {"missed": missed, "tombstoned": tombstoned}


def job_hard_status(conn: Conn, job_id: int) -> str:
    row = conn.execute("SELECT hard_status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return "pending"
    return str(row["hard_status"])


_NOTIFY_KINDS = frozenset({"urgent", "digest"})


def stamp_notified(conn: Conn, job_id: int, kind: str) -> None:
    notify_kind = kind if kind in _NOTIFY_KINDS else "digest"
    with transaction(conn):
        conn.execute(
            """
            UPDATE jobs
            SET notified_at = ?, notify_kind = ?
            WHERE id = ?
            """,
            (now(), notify_kind, int(job_id)),
        )
