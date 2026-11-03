# Design decisions and rationale

Why the code is shaped the way it is. Each entry states the decision, the alternative that was *not* taken, and the
evidence in the repository. Decisions marked *(inferred)* are reconstructed from the code and its tests rather than
stated by the authors.

## 1. Stages communicate only through the database

**Decision.** `collect`, `score`, `digest`, `contacts`, `sheet-sync` and the bot never call each other's internals; they
read and write rows. The one exception is `digest.run`, which invokes `score.run` (it needs fresh scores before selecting).
**Why.** Each stage can be run, retried, tested and scheduled independently; a crash in one leaves consistent rows for the
next. **Evidence.** `cli.py` wires one command per stage; `digest.run` calls `score_run`; stages share `db/`.
See [data-model](data-model.md).

## 2. Code decides eligibility; the LLM only advises

**Decision.** Three deterministic layers sit around the model: regex gates at ingest (`hard_filter.hard_filter`), post-hoc
overrides on its output (`score.coerce_relevant`), and a code-computed fit score that makes up 65% of the ranking
(`fit.quality_score`). **Alternative not taken.** Trusting the model's `relevant`/`score` fields.
**Why.** Observed failure: the LLM scored almost every plausible job 90, so ranking carried no signal, and it accepted
HR and experienced roles. A deterministic score is explainable, testable and free. **Evidence.** The calibration paragraph
in `score._system_prompt`; `tests/test_fit.py` (identical LLM scores still rank differently);
`tests/test_hard_filter.py` quality-gate cases. See [hard-filter](../modules/filtering/hard-filter.md),
[score-llm](../modules/ranking/score-llm.md), [fit-and-tiers](../modules/ranking/fit-and-tiers.md).

## 3. Filter at write time, inside `upsert_job`

**Decision.** `hard_filter` and the legitimacy check run every time a job is upserted and their results are stored
(`hard_status`, `hard_reason`, `legitimacy_flag`). **Alternative.** Filter at read time.
**Why.** Later queries are plain SQL on indexed columns (`jobs_hard_active`), and the reason a job was rejected is
inspectable in the table. **Cost.** A profile or regex change only takes effect for jobs that are seen again.
See [storage/jobs](../modules/storage/jobs.md#upsert_job).

## 4. Public ATS JSON, not scraping, as the primary source

**Decision.** Six ATS vendors are read through their public board APIs with plain `httpx`; scraping (Scrapling,
Playwright) is a secondary lane for a few portals and is only imported when needed. **Why.** Structured JSON is stable,
cheap, polite and needs no browser. **Evidence.** `ats/` adapters, `collect._collect_side_lanes` lazy imports,
`tests/test_no_stealthy.py`, `tests/test_scaffold_constraints.py`. See [ats-overview](../modules/sources/ats-overview.md).

## 5. Tombstoning needs a *complete* fetch and two misses

**Decision.** A job is closed only after it is absent from two consecutive **complete** fetches of its source.
**Why.** A single transient error, a partial page, or an empty response must not delete real jobs.
`FetchResult.complete` carries the guarantee from adapter to sweep. **Evidence.** `db/jobs.bump_unseen_for_source`
(`new_miss >= 2`), Greenhouse/SmartRecruiters/Lever `complete` handling, `tests/test_tombstone.py`.
**Known gap.** Portals/feeds/marketplaces always sweep with `success=True`; see
[known issues](known-issues-and-limitations.md).

## 6. Dependency injection instead of mocking

**Decision.** Network, LLM, Telegram and Sheets are passed as arguments (`client`, `fetch_fn`, `chat_fn`, `http_post`,
`worksheet`, `portal_run_fn`...). **Why.** Tests run offline in seconds, and the production code has no test-only branches
(an earlier `PYTEST_CURRENT_TEST` check was removed in favour of a conftest fixture). **Evidence.** `tests/conftest.py`,
`tests/test_pipeline_e2e.py`. See [testing strategy](../testing/strategy.md).

## 7. One database layer for SQLite and Postgres

**Decision.** `db/connection.py` wraps a psycopg connection in `_PgConnection` that rewrites `?` placeholders to `%s`,
appends `RETURNING id` to inserts for known tables, and mimics `executescript`. **Alternative.** An ORM or two query sets.
**Why.** The same SQL strings serve local SQLite and hosted Postgres with no third dependency. **Trade-off.** The rewrite
is string-based and guarded by `safe_ident` and a table allow-list. See
[connection-and-postgres-shim](../modules/storage/connection-and-postgres-shim.md).

## 8. A cron *gate* job instead of a fixed schedule

**Decision.** The workflow ticks every 15 minutes; a stdlib-only script decides whether the digest or urgent slot is due.
**Why.** The send time lives in a secret (`JOBDIGEST_CRON`) so it can change without a code change, and GitHub cron
strings cannot be parameterised. See [deployment](deployment-and-scheduling.md).

## 9. Buttons require a poller, so the poller is a workflow

**Decision.** `bot.yml` long-polls for ~55 minutes per run and restarts hourly, and every tap path ends with a visible
message. **Why.** Telegram callbacks are only delivered to a process that calls `getUpdates`; without one the buttons
do nothing. **Constraint.** It needs the same database as the pipeline (`DATABASE_URL`). See
[delivery/bot](../modules/delivery/bot.md).

## 10. The digest prefers fewer, better, newer jobs

**Decision.** `rank.select_diverse` applies a quality floor, a posting-age limit, one role per company, a per-aggregator
cap and a company cooldown (bypassed for quality >= 85), and returns *fewer* rows rather than padding.
**Why.** The recipient wants real, new, varied opportunities, not the same company repeatedly. Persisted state
(`notified_at`) is what makes "new" and "cooldown" possible, which is why the pipeline keeps a state file between runs.
See [rank-and-selection](../modules/ranking/rank-and-selection.md).

## 11. Draft outreach, never send it *(inferred)*

**Decision.** `email_draft` renders a Subject/To/Body message and a `mailto:` link into Telegram; there is no SMTP code.
**Why *(inferred)*.** Guessed addresses can be wrong and a person should review any message sent in their name.
See [email-drafts](../modules/delivery/email-drafts.md).

## 12. Defaults that fail safe

Collect failures alert loudly; optional integrations skip; secrets never appear in logs (Telegram token errors are
reduced to the exception type). Reasoning and mechanics are in
[error handling](error-handling-and-observability.md).
