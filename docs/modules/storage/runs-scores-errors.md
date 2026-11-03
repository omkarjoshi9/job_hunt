# Runs, scores and portal errors (`db/runs.py`)

- **Source**: `src/jobdigest/db/runs.py` (197 lines)
- **Layer**: persistence for pipeline bookkeeping (`runs`, `portal_errors`) and LLM results (`scores`).
- **Depends on**: `db._common` (`now`, `Conn`, `RowLike`), `db.connection.transaction`.
- **Used by**: `collect.py` (`insert_run`, `finish_run`, `insert_portal_error`), `score.py` (`insert_run`, `finish_run`, `unscored_pass_jobs`, `upsert_score`), `digest.py` (`insert_run`, `finish_run`, `collect_is_ok`), `contacts/run.py` (`insert_run`, `last_digest_job_ids`), `health.py` (`last_collect_run`).
- **Tests**: `tests/test_content_hash.py` (`test_unscored_pass_jobs_includes_stale`, `test_upsert_score_clears_stale`), `tests/test_dual_hash.py::test_unscored_skips_aliases`, `tests/test_health.py`, `tests/test_db_contacts.py::test_last_digest_job_ids`, `tests/test_digest.py`, `tests/test_urgent.py`.

## Why this exists

Each pipeline stage (`collect`, `score`, `digest`, `contacts`) records a row in `runs` with start/finish time, status and a JSON stats blob. Later stages use those rows as a contract: the digest refuses to trust stale data unless the last `collect` run was OK and recent, and the contacts stage reads which jobs the last digest sent from `stats_json`. `scores` stores LLM verdicts keyed by `(job_id, model, prompt_version)`, and `unscored_pass_jobs` is the work queue for the scorer.

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `insert_run` | `(conn, kind, status="running") -> int` | start a run row |
| `finish_run` | `(conn, run_id, status, stats, error=None)` | close it with stats |
| `insert_portal_error` | `(conn, *, run_id, portal_name, url, error_class, http_status, detail)` | log a portal failure |
| `last_collect_run` | `(conn) -> RowLike \| None` | newest collect run |
| `collect_is_ok` | `(conn, *, max_age_hours=26) -> bool` | freshness gate for digest |
| `last_ok_run` | `(conn, kind) -> RowLike \| None` | newest OK run of a kind |
| `last_digest_job_ids` | `(conn) -> list[int]` | job ids from the last OK digest |
| `unscored_pass_jobs` | `(conn, model, prompt_version) -> list[RowLike]` | scoring queue |
| `upsert_score` | `(conn, job_id, model, prompt_version, relevant, score, reasons)` | write a score |

## Detailed walkthrough

### `insert_run`
**Location** `src/jobdigest/db/runs.py:11` · `def insert_run(conn: Conn, kind: str, status: str = "running") -> int`
Within `transaction`: `INSERT INTO runs (kind, started_at, status) VALUES (?, ?, ?)` with `now()`; returns `int(cur.lastrowid)`. `status` must satisfy the CHECK (`running|ok|error`) or the INSERT fails. **Called by** `collect.py:557` (`"collect"`, `"running"`), `digest.py:368` (`"digest"`), `score.py:315` (`"score"`), `contacts/run.py:90` (`"contacts"`).

### `finish_run`
**Location** `src/jobdigest/db/runs.py:20`
`UPDATE runs SET finished_at = ?, status = ?, stats_json = ?, error = ? WHERE id = ?` with `now()`, the status, `json.dumps(stats, separators=(",", ":"))`, and the optional error text. Runs in a transaction. No check that the row exists or is still `running`; calling it twice overwrites. Typical stats: collect writes counters (`fetched, inserted, updated, skipped, pass, reject, errors, missed, tombstoned`); digest writes `{"guest": n, "path": ..., "job_ids": [...]}`, the latter consumed by `last_digest_job_ids`.

### `insert_portal_error`
**Location** `src/jobdigest/db/runs.py:38`: `INSERT INTO portal_errors (run_id, portal_name, url, error_class, http_status, detail, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)` with `now()`, in a transaction. **Called by** `collect.py:276`. `health.py` later queries this table by `run_id` (for example `error_class = 'parse'` and `detail LIKE '%0 cards%'` in `health.py`) to report portals that returned zero cards.

### `_parse_iso`
`runs.py:59`: strips, rewrites a trailing `Z` to `+00:00`, `datetime.fromisoformat`, assigns UTC if naive, converts to UTC. Raises `ValueError` on garbage (caught by `collect_is_ok`). Separate from `followups.parse_iso`, which returns `None` instead of raising.

### `last_collect_run`
`runs.py:69`: `SELECT * FROM runs WHERE kind = 'collect' ORDER BY id DESC LIMIT 1`, any status. **Called by** `collect_is_ok`, `health.py:69`.

### `collect_is_ok`
**Location** `src/jobdigest/db/runs.py:75` · `def collect_is_ok(conn: Conn, *, max_age_hours: float = 26) -> bool`
Gate used by `digest.py:370` (`collect_ok = collect_is_ok(conn)`) to know whether the pool is fresh.
1. `row = last_collect_run(conn)`; `None` -> `False`.
2. `row["status"] != "ok"` -> `False` (a `running` or `error` newest run counts as not OK, even if an earlier one was fine).
3. `finished = row["finished_at"]` empty -> `False`.
4. `_parse_iso(finished)`; `ValueError`/`TypeError` -> `False`.
5. `age = datetime.now(timezone.utc) - dt`; return `age.total_seconds() <= max_age_hours * 3600`. 26 hours = a daily collect plus slack. Note this uses the real clock, not the patchable `now()`.

### `last_ok_run`
`runs.py:92`: `SELECT * FROM runs WHERE kind = ? AND status = 'ok' ORDER BY id DESC LIMIT 1`.

### `last_digest_job_ids`
**Location** `src/jobdigest/db/runs.py:105` · `def last_digest_job_ids(conn: Conn) -> list[int]`
**Called by** `contacts/run.py:31` when no explicit job ids are given.
1. `row = last_ok_run(conn, "digest")`; `None` -> `[]`.
2. `raw = row["stats_json"]`; empty -> `[]`.
3. `json.loads(raw)`; `TypeError/JSONDecodeError` -> `[]`; non-dict -> `[]`.
4. `ids = stats.get("job_ids")`; non-list -> `[]`.
5. For each item: `int(item)` (skip on `TypeError/ValueError`); keep only `jid > 0`.
Defensive by design: stats are free-form JSON. Test: `test_last_digest_job_ids`. Note the urgent slot also writes `job_ids` (`digest._run_urgent`) under run kind `digest`, so "last OK digest run" may be an urgent run (the code does not distinguish them; run `kind` is `"digest"` for both as far as `insert_run(conn, "digest")` at `digest.py:368` shows).

### `unscored_pass_jobs`
**Location** `src/jobdigest/db/runs.py:132` · `def unscored_pass_jobs(conn, model: str, prompt_version: str) -> list[RowLike]`
**Called by** `score.py:317`, which sends each row to the LLM.
```sql
SELECT jobs.* FROM jobs
WHERE jobs.hard_status = 'pass' AND jobs.is_active = 1 AND jobs.canonical_job_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM scores WHERE scores.job_id = jobs.id AND scores.model = ?
                  AND scores.prompt_version = ? AND scores.stale = 0)
  AND NOT EXISTS (SELECT 1 FROM applications WHERE applications.apply_url_norm = jobs.apply_url_norm)
  AND NOT EXISTS (SELECT 1 FROM applications a JOIN jobs j2 ON j2.apply_url_norm = a.apply_url_norm
                  WHERE jobs.posting_key IS NOT NULL AND jobs.posting_key != ''
                    AND j2.posting_key = jobs.posting_key)
ORDER BY jobs.id ASC
```
Conditions in order: survived the hard filter; still active; is a canonical row (aliases are not scored); has no **fresh** score for this exact model and prompt version (a stale score, or a score from another model/version, counts as unscored, so changing `prompt_version` re-scores everything and a changed JD triggers re-scoring); the job's own URL has no application; and no *other* job with the same `posting_key` has an application (applying via a portal copy suppresses scoring of the ATS copy; `test_application_on_alias_suppresses_canonical_guest`). `_relevant_unapplied_select` repeats these filters; see [contacts-and-digest-queries.md](contacts-and-digest-queries.md#_relevant_unapplied_select). Ordered by id, so oldest first. Verified offline: after a job was tombstoned the function returned `0` rows.

### `upsert_score`
**Location** `src/jobdigest/db/runs.py:166` · `def upsert_score(conn, job_id: int, model: str, prompt_version: str, relevant: bool, score: float, reasons: dict) -> None`
**Called by** `score.py:214`.
In a transaction:
```sql
INSERT INTO scores (job_id, model, prompt_version, relevant, score, reasons_json, created_at, stale)
VALUES (?, ?, ?, ?, ?, ?, ?, 0)
ON CONFLICT(job_id, model, prompt_version) DO UPDATE SET
  relevant = excluded.relevant, score = excluded.score, reasons_json = excluded.reasons_json,
  created_at = excluded.created_at, stale = 0
```
Parameters are coerced: `relevant` to `1/0`, `score` to `float`, `reasons` to compact JSON, timestamp `now()`. The conflict target is the table's `UNIQUE(job_id, model, prompt_version)`, so re-scoring replaces the row and clears `stale`. Valid on both engines; on Postgres the shim appends `RETURNING id` (harmless). Test: `test_upsert_score_clears_stale`. The score scale is 0-100 as far as the digest thresholds are concerned (in my offline run a score of 9.0 did not pass the digest quality bar of 50.0 while 95.0 did).

## Failure modes and edge cases

- A run left in `running` (crash before `finish_run`) makes `last_collect_run` return it, so `collect_is_ok` is `False` until a new collect finishes.
- `scores` rows are never deleted; old model/version rows accumulate and are ignored by queries filtering on the current pair.
- `finish_run` does not validate `status`; the CHECK constraint is the only guard.

## How to modify / extend safely

Add new run kinds freely (`kind` is unchecked text); document their `stats_json` shape if another stage reads it. If you change the scoring-eligibility rules in `unscored_pass_jobs`, mirror them in `digest_queries._relevant_unapplied_select`, otherwise jobs can be scored but never shown, or shown without scoring.
