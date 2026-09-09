from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from jobdigest.config import load_models, load_profile
from jobdigest.db import (
    connect,
    ensure_application,
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
CAMPUS_CHOICES = ("offcampus", "oncampus")
APPLIED_CHOICES = ("yes", "no")
HEADER_RANGE = "A1:I1"
CAMPUS_RANGE = "F2:F5000"
APPLIED_RANGE = "G2:G5000"
LINK_COL = 3
HEADER_FORMAT = {
    "backgroundColor": {"red": 0.15, "green": 0.15, "blue": 0.18},
    "horizontalAlignment": "CENTER",
    "verticalAlignment": "MIDDLE",
    "textFormat": {
        "bold": True,
        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
    },
}


def require_sheet() -> bool:
    raw = (os.environ.get("JOBDIGEST_REQUIRE_SHEET") or "").strip().lower()
    return raw in {"1", "true", "yes"}


_SHEET_FAILED = "SHEET FAILED missing GOOGLE_SHEET_ID or GOOGLE_SERVICE_ACCOUNT_JSON"


def _missing_sheet_creds() -> bool:
    sheet_id = (os.environ.get("GOOGLE_SHEET_ID") or "").strip()
    json_path = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    return not sheet_id or not json_path


def _resolve_worksheet(kind: str, worksheet: Any | None) -> tuple[Any | None, int | None]:
    if worksheet is not None:
        return worksheet, None
    if _missing_sheet_creds():
        if require_sheet():
            print(_SHEET_FAILED, file=sys.stderr)
            return None, 1
        print(f"{kind} skip missing credentials")
        return None, 0
    try:
        return open_worksheet(), None
    except Exception as exc:
        print(f"{kind} error {exc}", file=sys.stderr)
        print(f"{kind} error")
        return None, 1


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


def _call_sheet(worksheet: Any, name: str, *args: Any, **kwargs: Any) -> None:
    method = getattr(worksheet, name, None)
    if not callable(method):
        return
    try:
        method(*args, **kwargs)
    except Exception as exc:
        print(f"sheet-{name} skip {exc}", file=sys.stderr)


def apply_sheet_layout(worksheet: Any) -> None:
    _call_sheet(worksheet, "format", HEADER_RANGE, HEADER_FORMAT)
    _call_sheet(worksheet, "freeze", rows=1)
    try:
        from gspread.utils import ValidationConditionType
    except Exception as exc:
        print(f"sheet-validation skip {exc}", file=sys.stderr)
        return
    _call_sheet(
        worksheet,
        "add_validation",
        CAMPUS_RANGE,
        ValidationConditionType.one_of_list,
        list(CAMPUS_CHOICES),
        showCustomUi=True,
        strict=False,
    )
    _call_sheet(
        worksheet,
        "add_validation",
        APPLIED_RANGE,
        ValidationConditionType.one_of_list,
        list(APPLIED_CHOICES),
        showCustomUi=True,
        strict=False,
    )


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
        else:
            worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")
        apply_sheet_layout(worksheet)
        return
    first = _header_row(values)
    if first[: len(HEADERS)] != HEADERS:
        if changed:
            _replace_values(worksheet, migrated)
        else:
            updater = getattr(worksheet, "update", None)
            if callable(updater):
                worksheet.update("A1:I1", [HEADERS])
            else:
                worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")
    apply_sheet_layout(worksheet)


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


def _ensure_saved_application(conn: Any, job: Any, link: str, sheet_row: int) -> None:
    _action, app_id = ensure_application(
        conn,
        apply_url=link,
        company=str(job["company"] or ""),
        title=str(job["title"] or ""),
        status="saved",
        source=str(job["source"] or ""),
        job_id=int(job["id"]),
    )
    if app_id is not None:
        mark_sheet_synced(conn, app_id, sheet_row)


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
        ws, gate_code = _resolve_worksheet("sheet-append", worksheet)
        if gate_code is not None:
            return gate_code
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
        ws, gate_code = _resolve_worksheet("sheet-sync", worksheet)
        if gate_code is not None:
            return gate_code
        ensure_headers(ws)
        values = list(ws.get_all_values() or [])
        if not values:
            values = [list(HEADERS)]
        existing = _existing_norms(values)
        models = load_models()
        profile = load_profile()
        jobs = relevant_unapplied_jobs(
            conn,
            models.score_model,
            models.score_prompt_version,
            profile=profile,
        )
        date = utc_now()[:10]
        limit = max(int(profile.digest_max), 0)
        for job in jobs:
            link = _sheet_link(job)
            if link is None:
                continue
            norm = normalize_url(link)
            if norm in existing:
                skipped_existing += 1
                _ensure_saved_application(conn, job, link, existing[norm])
                continue
            if appended >= limit:
                continue
            values_row = _guest_row(job, date)
            ws.append_row(values_row, value_input_option="USER_ENTERED")
            values.append(values_row)
            sheet_row = len(values)
            existing[norm] = sheet_row
            _ensure_saved_application(conn, job, link, sheet_row)
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
