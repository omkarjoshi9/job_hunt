# Follow-ups and health (`followups.py`, `health.py`)

- Source: `src/jobdigest/followups.py` (96 lines), `src/jobdigest/health.py` (124 lines)
- Layer: delivery helpers. Both turn database state into a short plain-text report. They are triggered by the bot slash commands `/followups` and `/health` ([bot.md](bot.md#handle_update)); `health` is additionally printed by `collect` as a footer.
- Depends on: `db.last_collect_run` (health), nothing else (followups uses raw SQL on the `applications` and `jobs` tables).
- Used by: `bot.handle_update`; `collect.py` (imports `HEALTHY`, `collect_health`, `format_health`, `src/jobdigest/collect.py:40`, footer at `:593`); `digest.py` and `db/digest_queries.py` reuse `parse_iso` and `ist_date` from `followups.py` (so `followups.py` is a tiny shared time utility as well as a feature).
- Tests: `tests/test_followups.py`, `tests/test_health.py`; the bot commands in `tests/test_bot.py::test_handle_update_followups_replies`, `_followup_alias`, `_followups_empty_still_replies`, `test_handle_update_health_replies`.

---

# Part 1: Follow-ups

## Why this exists

After you mark a job Applied (button or `sheet-append`), the reminder you want is "nothing heard in about a week, nudge them". `/followups` lists up to five applications that have been in `applied` status for at least seven **business days** in India time.

## Public interface

| Symbol | Signature | Purpose |
|---|---|---|
| `IST` | `timezone(timedelta(hours=5, minutes=30))` | India Standard Time offset (no DST). |
| `FOLLOWUP_LIMIT` | `5` | Max rows listed. |
| `FOLLOWUP_BUSINESS_DAYS` | `7` | Age threshold. |
| `parse_iso` | `(value: str \| None) -> datetime \| None` | Lenient ISO parser, always UTC-aware. |
| `ist_date` | `(value: datetime) -> date` | Calendar date in IST. |
| `ist_business_days_age` | `(applied_on, now) -> int` | Weekdays elapsed (Mon-Fri), IST. |
| `ist_calendar_days_age` | `(applied_on, now) -> int` | Calendar days elapsed, IST. |
| `due_followups` | `(conn, *, now, limit=FOLLOWUP_LIMIT) -> list[RowLike]` | The rows due. |
| `format_followups` | `(rows, *, now) -> str` | Message text. |

## Walkthrough

### `parse_iso`

**Location** `src/jobdigest/followups.py:13`. `value.strip().replace("Z", "+00:00")` then `datetime.fromisoformat`; `TypeError`, `ValueError`, `AttributeError` (i.e. `None`, non-strings, garbage) all return `None`. A naive datetime is assumed to be UTC; the result is converted to UTC. Callers across the codebase use it for `first_seen_at`, `notified_at`, `applied_on` values. (Python's `fromisoformat` handles `Z` natively only from 3.11, but the replacement keeps it explicit.)

### `ist_date`

**Location** `:24`. Naive input is treated as UTC; converts to `IST` and returns `.date()`. Business logic uses the *IST calendar day*, so a job applied at 22:00 UTC on a Monday is already Tuesday in India.

### `ist_business_days_age`

**Location** `:31` · `def ist_business_days_age(applied_on: str | None, now: datetime) -> int`

1. `start_dt = parse_iso(applied_on)`; unparseable -> `0`.
2. `start = ist_date(start_dt)`, `end = ist_date(now)`. If `end <= start` -> `0`.
3. Walk the days after `start` up to and including `end`; count those where `cursor.weekday() < 5`.

```python
while cursor < end:
    cursor += one
    if cursor.weekday() < 5:
        n += 1
```

The start day itself is never counted; weekends and nothing else are excluded (no public-holiday calendar). Verified: applied Mon 2026-10-26, now Tue 2026-11-03 gives `6`; tests assert Monday to next Monday is 5, Monday to Wednesday of the following week is 7, Friday to the following Tuesday is 7 (`tests/test_followups.py::test_ist_business_days_*`). Cost is linear in elapsed days, which is fine at this scale.

### `ist_calendar_days_age`

**Location** `:49`. `(ist_date(now) - ist_date(start)).days` clamped at `0`; `0` when unparseable. Used only for the "applied Nd" text.

### `due_followups`

**Location** `:57` · `def due_followups(conn: Any, *, now: datetime, limit: int = FOLLOWUP_LIMIT) -> list[RowLike]`

1. One query: `SELECT applications.*, jobs.apply_url AS job_apply_url FROM applications LEFT JOIN jobs ON jobs.id = applications.job_id WHERE applications.status = 'applied' ORDER BY applications.applied_on ASC, applications.id ASC`. Only `applied`; saved, hidden, `oa`, `interview` and so on are excluded (`test_due_followups_excludes_saved_hidden_oa`).
2. `cap = max(int(limit), 0)`.
3. Iterate oldest first; skip rows whose `ist_business_days_age(row["applied_on"], now) < FOLLOWUP_BUSINESS_DAYS`; append the rest; stop at `cap`. Age is computed in Python, not SQL, because it depends on weekday arithmetic in IST.
4. Returns the rows: the oldest applications first, at most five (`test_due_followups_caps_at_five_oldest`; `test_due_followups_six_days_absent_seven_present` checks the boundary).

### `format_followups`

**Location** `:80`. Empty list -> `"No follow-ups due."`. For each row builds `"<company> — <title>"`, then a URL line, then `applied <n>d` (calendar days). The URL is `job_apply_url` if it begins with http(s), else the application's `apply_url_norm` if that does, else the URL line is omitted. Blocks are joined with a blank line. The bot sends the result with `deliver(..., required=True)`.

---

# Part 2: Health

## Why this exists

`collect` fetches hundreds of ATS boards and several portals. Individual failures are normal and silent. `collect_health` summarises *which sources are persistently unhealthy* so a human can fix or disable them. Output appears (a) as a footer of `collect` when unhealthy and (b) on demand via `/health`.

## Public interface

| Symbol | Purpose |
|---|---|
| `HealthReport` (dataclass) | Four lists of one-line strings: `errored`, `empty_boards`, `zero_cards`, `dead_tokens`; `is_healthy()` is true when all four are empty. |
| `collect_health(conn) -> HealthReport` | Query the DB and classify. |
| `format_health(report) -> str` | Render, or the constant `HEALTHY = "All sources healthy."`. |
| `_SECTION_CAP = 15` | Max lines per section. |
| `_cell`, `_line` | Row helpers. |

## Walkthrough

### `HealthReport`

**Location** `src/jobdigest/health.py:17` · **Signature** `@dataclass class HealthReport` with one method, `is_healthy(self) -> bool`
**Purpose** A plain container for the four problem categories `collect_health` finds, so formatting and the "everything is fine" decision are separate from the SQL.
**Fields** (all `list[str]`, each created with `field(default_factory=list)`, so every instance gets its own lists): `errored` (boards or portals with an error), `empty_boards` (healthy fetch, zero jobs), `zero_cards` (portal parse errors reporting 0 cards), `dead_tokens` (boards whose token has been dead for at least two consecutive fetches). Each entry is one pre-formatted line (see `_line`), not a record.
**Lifecycle** `collect_health` creates `HealthReport()`, appends lines, truncates each list to `_SECTION_CAP` (15) and returns it. `format_health` renders it. Used by `collect.run` (`collect.py:593`, the health message at the end of a collect run) and the bot (`bot.py:449`, the health command). No other state; nothing is persisted.
**`is_healthy`** returns `not (self.errored or self.empty_boards or self.zero_cards or self.dead_tokens)`, i.e. true only when all four lists are empty; `format_health` returns the constant `HEALTHY` (`"All sources healthy."`) in that case.
Verified offline: `HealthReport().is_healthy()` is `True`; after `a.errored.append("x")` it is `False` for `a` and still `True` for a second instance (lists not shared); `HealthReport(dead_tokens=["d"]).is_healthy()` is `False`.

### `_cell` and `_line`

`_cell(row, name, default="")` (`src/jobdigest/health.py:29`) reads a column defensively (missing column or `None` -> default, with a debug log). `_line(row, extra="")` (`:40`) builds one report line: it joins the non-empty parts of `slug` (or `portal_name`), `ats`, `last_http_status` (or `http_status`) and `extra` with spaces. Example: `"boomco greenhouse 500 timeout"`.

### `collect_health`

**Location** `:48` · `def collect_health(conn: Conn) -> HealthReport`

**Step-by-step**

1. Select all **enabled** companies (`WHERE enabled = 1 ORDER BY slug`) with `last_error`, `last_job_count`, `dead_streak`, `last_http_status`.
2. Classify each board by this exact order:
   - `err == "dead_token" and streak >= 2` -> `dead_tokens`, line gets `streak=<n>`. The ATS reported "not found" at least twice in a row (see [collect](../collection/collect-orchestrator.md)). A dead token with `streak < 2` falls through *every* branch and is not reported (the `elif err and err != "dead_token"` excludes it): a single miss could be transient.
   - `err` non-empty and not `dead_token` -> `errored`, with `err[:80]`.
   - `err == ""` and `last_job_count == 0` -> `empty_boards` (`"... empty"`): a healthy fetch that returned zero jobs. A never-fetched board (`last_job_count` is `NULL`) is not reported.
3. `last = last_collect_run(conn)`; if none exists, skip the portal part. Otherwise, using that run's id:
   - `zeros`: `portal_errors` rows with `error_class = 'parse'` and `detail LIKE '%0 cards%'` become `zero_cards` lines `"<portal> parse <detail[:80]>"`. A "0 cards" parse error normally means the portal's HTML layout changed ([portals](../sources/portals-parsers.md)).
   - `extras`: all other `portal_errors` for the run. They are appended to `errored`, skipping a portal name already present as the first token of an existing `errored` line (`seen`) and de-duplicating by name, so one portal yields at most one line.
4. Each list is truncated to `_SECTION_CAP` (15).
5. Returns the report.

`test_health_classifies_error_empty_zero_dead` builds one board of each kind plus a `hasjob` 0-cards portal error and asserts each lands in the right list; `test_health_dead_token_requires_streak_2` covers the streak rule.

### `format_health`

**Location** `:112`. Healthy -> `HEALTHY`. Otherwise blocks in this fixed order, joined by a blank line: `ERRORS`, `EMPTY`, `0 CARDS`, `DEAD TOKENS`, each as header, newline, lines. Verified:

```text
ERRORS
a gh 500 x

DEAD TOKENS
b lever 404 streak=2
```

## Failure modes and edge cases

- `/health` reflects only the *latest collect run's* portal errors; earlier runs are not consulted.
- No public holidays in follow-up aging; weekdays only.
- Both reports can exceed the Telegram limit only in extreme cases; the bot's `deliver` splits long output ([telegram.md](telegram.md#split_message)).

## How to modify / extend safely

- Change the reminder delay by editing `FOLLOWUP_BUSINESS_DAYS` (the tests encode 7, so update them).
- Add a health category by adding a field to `HealthReport`, extending `is_healthy`, `collect_health` and `format_health` together; keep the cap.
