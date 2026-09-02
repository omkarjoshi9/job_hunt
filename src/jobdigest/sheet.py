from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    connect,
    mark_sheet_synced,
    pending_sheet_rows,
    relevant_unapplied_jobs,
    repo_root,
    upsert_application,
    utc_now,
)
from jobdigest.envfile import load_dotenv
from jobdigest.normalize import normalize_url

OLD_HEADERS = ["Date", "Company", "Role", "Link", "Source", "Status", "Notes"]
HEADERS = [
    "Date",
    "Company",
    "Role",
    "Link",
    "Source",
    "Campus",
    "Applied",
    "Status",
    "Notes",
]
CAMPUS_OFF = "offcampus"
LINK_COL = 3


def require_sheet() -> bool:
    raw = (os.environ.get("JOBDIGEST_REQUIRE_SHEET") or "").strip().lower()
    return raw in {"1", "true", "yes"}


def open_worksheet():
    load_dotenv()
    sheet_id = (os.environ.get("GOOGLE_SHEET_ID") or "").strip()
    json_path = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not sheet_id or not json_path:
        raise RuntimeError("missing GOOGLE_SHEET_ID or GOOGLE_SERVICE_ACCOUNT_JSON")
    path = Path(json_path).expanduser()
    if not path.is_absolute():
        path = repo_root() / path
    if not path.is_file():
        raise RuntimeError(f"service account JSON not found: {path}")
    import gspread

    gc = gspread.service_account(filename=str(path))
    return gc.open_by_key(sheet_id).sheet1


def _header_row(values: list[list[str]]) -> list[str]:
    if not values:
        return []
    return [str(c).strip() for c in values[0]]


def migrate_if_needed(values: list[list[str]]) -> tuple[list[list[str]], bool]:
    if not values:
        return [list(HEADERS)], True
    first = [str(c).strip() for c in values[0][:9]]
    if first[: len(HEADERS)] == HEADERS:
        return values, False
    old = [str(c).strip() for c in values[0][:7]]
    if old != OLD_HEADERS:
        return values, False
    out = [list(HEADERS)]
    for row in values[1:]:
        padded = [str(c) if c is not None else "" for c in row] + [""] * 7
        out.append(
            [
                padded[0],
                padded[1],
                padded[2],
                padded[3],
                padded[4],
                CAMPUS_OFF,
                "",
                padded[5],
                padded[6],
            ]
        )
    return out, True


def _replace_values(worksheet: Any, rows: list[list[str]]) -> None:
    updater = getattr(worksheet, "update", None)
    if not callable(updater):
        raise RuntimeError("worksheet cannot update cells")
    try:
        worksheet.update("A1", rows)
    except TypeError:
        worksheet.update(rows)


def ensure_headers(worksheet: Any) -> None:
    values = list(worksheet.get_all_values() or [])
    migrated, changed = migrate_if_needed(values)
    if not values:
        updater = getattr(worksheet, "update", None)
        if callable(updater):
            worksheet.update("A1:I1", [HEADERS])
            return
        worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")
        return
    first = _header_row(values)
    if first[: len(HEADERS)] == HEADERS:
        return
    if changed:
        _replace_values(worksheet, migrated)
        return
    updater = getattr(worksheet, "update", None)
    if callable(updater):
        worksheet.update("A1:I1", [HEADERS])
        return
    if not values:
        worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")


def _existing_norms(values: list[list[str]]) -> dict[str, int]:
    existing: dict[str, int] = {}
    for i, row in enumerate(values):
        if i == 0:
            continue
        link = row[LINK_COL] if len(row) > LINK_COL else ""
        text = str(link or "").strip()
        if not text:
            continue
        if not (
            text.lower().startswith("http://") or text.lower().startswith("https://")
        ):
            continue
        existing[normalize_url(text)] = i + 1
    return existing


def _sheet_link(row: Any) -> str | None:
    job_url = ""
    try:
        job_url = str(row["job_apply_url"] or "")
    except (KeyError, IndexError):
        job_url = ""
    if job_url.lower().startswith("http://") or job_url.lower().startswith("https://"):
        return job_url
    try:
        apply_url = str(row["apply_url"] or "")
    except (KeyError, IndexError):
        apply_url = ""
    if apply_url.lower().startswith("http://") or apply_url.lower().startswith("https://"):
        return apply_url
    norm = str(row["apply_url_norm"] or "")
    if norm.lower().startswith("http://") or norm.lower().startswith("https://"):
        return norm
    return None


def _guest_row(job: Any, date: str) -> list[str]:
    link = _sheet_link(job) or ""
    return [
        date,
        str(job["company"] or ""),
        str(job["title"] or ""),
        link,
        str(job["source"] or ""),
        CAMPUS_OFF,
        "",
        "",
        "",
    ]


def run(
    *,
    db_path: Path | None = None,
    url: str | None = None,
    company: str = "",
    title: str = "",
    status: str = "applied",
    notes: str = "",
    source: str = "",
    worksheet: Any | None = None,
) -> int:
    load_dotenv()
    conn = connect(db_path)
    appended = 0
    synced_existing = 0
    skipped = 0
    try:
        if url:
            upsert_application(
                conn,
                apply_url=url,
                company=company,
                title=title,
                status=status,
                notes=notes,
                source=source,
            )
        pending = pending_sheet_rows(conn)
        pending_n = len(pending)
        ws = worksheet
        if ws is None:
            try:
                ws = open_worksheet()
            except Exception as exc:
                print(f"sheet-append error {exc}", file=sys.stderr)
                print("sheet-append error")
                return 1
        ensure_headers(ws)
        values = list(ws.get_all_values() or [])
        if not values:
            values = [list(HEADERS)]
        existing = _existing_norms(values)
        for row in pending:
            link = _sheet_link(row)
            if link is None:
                skipped += 1
                print("sheet-append skip non-http link")
                continue
            norm = str(row["apply_url_norm"])
            if norm in existing:
                mark_sheet_synced(conn, int(row["id"]), existing[norm])
                synced_existing += 1
                continue
            date = str(row["applied_on"] or "")[:10]
            values_row = [
                date,
                str(row["company"] or ""),
                str(row["title"] or ""),
                link,
                str(row["source"] or ""),
                CAMPUS_OFF,
                "",
                str(row["status"] or "applied"),
                str(row["notes"] or ""),
            ]
            ws.append_row(values_row, value_input_option="USER_ENTERED")
            values.append(values_row)
            sheet_row = len(values)
            mark_sheet_synced(conn, int(row["id"]), sheet_row)
            existing[normalize_url(link)] = sheet_row
            appended += 1
        print(
            f"sheet-append ok pending={pending_n} appended={appended} "
            f"synced_existing={synced_existing} skipped={skipped}"
        )
        return 0
    except Exception as exc:
        print(f"sheet-append error {exc}", file=sys.stderr)
        print("sheet-append error")
        return 1
    finally:
        conn.close()


def sync(*, db_path: Path | None = None, worksheet: Any | None = None) -> int:
    load_dotenv()
    conn = connect(db_path)
    appended = 0
    skipped_existing = 0
    try:
        ws = worksheet
        if ws is None:
            try:
                ws = open_worksheet()
            except Exception as exc:
                print(f"sheet-sync error {exc}", file=sys.stderr)
                print("sheet-sync error")
                return 1
        ensure_headers(ws)
        values = list(ws.get_all_values() or [])
        if not values:
            values = [list(HEADERS)]
        existing = _existing_norms(values)
        models = load_models()
        profile = load_profile()
        jobs = relevant_unapplied_jobs(
            conn, models.score_model, models.score_prompt_version
        )
        date = utc_now()[:10]
        limit = max(int(profile.digest_max), 0)
        for job in jobs:
            if appended >= limit:
                break
            link = _sheet_link(job)
            if link is None:
                continue
            norm = normalize_url(link)
            if norm in existing:
                skipped_existing += 1
                continue
            values_row = _guest_row(job, date)
            ws.append_row(values_row, value_input_option="USER_ENTERED")
            values.append(values_row)
            existing[norm] = len(values)
            appended += 1
        print(
            f"sheet-sync ok appended={appended} skipped_existing={skipped_existing}"
        )
        return 0
    except Exception as exc:
        print(f"sheet-sync error {exc}", file=sys.stderr)
        print("sheet-sync error")
        return 1
    finally:
        conn.close()
