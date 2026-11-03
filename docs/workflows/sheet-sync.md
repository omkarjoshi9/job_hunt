# Workflow: `python -m jobdigest sheet-sync`

Mirrors relevant jobs into a Google Sheet that the user treats as a lightweight CRM. One-way: database -> sheet. Source:
`src/jobdigest/sheet.py`. Runs as the last step of the digest slot in `pipeline.yml` (not on the urgent slot).

## Trigger and entry point

`pipeline.yml` step *Sheet sync* (after *Write Google service account*) or `./start.sh` or by hand ->
`cli._cmd_sheet_sync` -> `sheet.sync(db_path=...)`. Its sibling `sheet-append` (`sheet.run`) writes a single tracked
application by URL; this page covers `sync`.

## Step by step (`sheet.sync`)

1. `load_dotenv()`, `connect(db_path)`.
2. `_resolve_worksheet("sheet-sync", worksheet)`:
   * an injected `worksheet` (tests use `FakeSheet`) is used as is;
   * missing `GOOGLE_SHEET_ID` or `GOOGLE_SERVICE_ACCOUNT_JSON` -> prints `sheet-sync skip missing credentials` and returns exit
     0, **unless** `JOBDIGEST_REQUIRE_SHEET` is truthy, in which case it prints `SHEET FAILED ...` and returns 1 (the hosted
     workflow sets it);
   * otherwise `open_worksheet()` authorises gspread with the service-account file (path resolved against the repo root) and
     opens `sheet1`; any exception prints `sheet-sync error` and returns 1.
3. `ensure_headers(ws)`: writes the 9-column header `Date, Company, Role, Link, Source, Campus, Applied, Status, Notes`
   (and migrates the older 7-column layout, defaulting Campus to `offcampus`); applies header formatting, a frozen header row
   and drop-downs for the Campus and Applied columns (`F2:F5000`, `G2:G5000`).
4. `values = ws.get_all_values()`; `existing = _existing_norms(values)` maps the **normalized Link column** (index 3) to its
   sheet row number.
5. `jobs = relevant_unapplied_jobs(conn, model, prompt_version, profile=profile)`: every job with a fresh relevant score and
   no application, ranked by blend rank (see [scoring-and-digest](scoring-and-digest.md)). Note this list is **not** passed
   through `select_diverse`, so the quality floor, age limit and per-company caps that shape the digest do not apply here.
6. For each job, in rank order:
   * `_sheet_link(job)` picks the apply URL (falling back to the normalized one); no link -> skip.
   * if `normalize_url(link)` is already in `existing` -> count `skipped_existing` and `_ensure_saved_application(...)`,
     which creates/updates an `applications` row with status `saved` and `sheet_row`, so the job stops appearing as a candidate;
   * else if fewer than `profile.digest_max` have been appended this run -> `ws.append_row(_guest_row(job, date),
     value_input_option="USER_ENTERED")`, record `sheet_row = len(values)` after appending, and
     `_ensure_saved_application`.
7. Prints `sheet-sync ok appended=N skipped_existing=M` and returns 0. Any exception prints `sheet-sync error ...` and
   returns 1.

## Why it works this way

* **Dedupe by link, not by id.** The sheet can be edited by hand, so the only stable key is the URL column.
* **`saved` applications.** Writing a `saved` application row is what makes a synced job disappear from later candidate lists
  (the selection queries exclude jobs that have an application), so each sync adds new jobs instead of repeating.
* **Rows per run are capped** to `digest_max` to avoid flooding the sheet.

## Failure and edge cases

| Situation | Result |
|---|---|
| No credentials, local | skip, exit 0 |
| No credentials, hosted (`JOBDIGEST_REQUIRE_SHEET=1`) | `SHEET FAILED`, exit 1 |
| Row 1 is not a recognised header | it is **overwritten** with the new header (`ensure_headers`) |
| Blank rows inside the sheet | the stored `sheet_row` (`len(values)`) may be off |
| Edits to Status/Applied/Notes in the sheet | never read back into the database |

Reference: [delivery/sheet](../modules/delivery/sheet.md). Tests: `tests/test_sheet.py` (with `FakeSheet`) and the e2e test in
`tests/test_pipeline_e2e.py`.
