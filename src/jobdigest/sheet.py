from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from jobdigest.db import (
    connect,
    mark_sheet_synced,
    pending_sheet_rows,
    repo_root,
    upsert_application,
)
from jobdigest.envfile import load_dotenv
from jobdigest.normalize import normalize_url

HEADERS = ["Date", "Company", "Role", "Link", "Source", "Status", "Notes"]


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


def ensure_headers(worksheet: Any) -> None:
    values = worksheet.get_all_values() or []
    first = [str(c).strip() for c in (values[0][:7] if values else [])]
    if first == HEADERS:
        return
    updater = getattr(worksheet, "update", None)
    if callable(updater):
        worksheet.update("A1:G1", [HEADERS])
        return
    if not values:
        worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")


def _existing_norms(values: list[list[str]]) -> dict[str, int]:
    existing: dict[str, int] = {}
    for i, row in enumerate(values):
        if i == 0:
            continue
        link = row[3] if len(row) > 3 else ""
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
    norm = str(row["apply_url_norm"] or "")
    if norm.lower().startswith("http://") or norm.lower().startswith("https://"):
        return norm
    return None


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
