# Known issues and limitations

Behaviour that surprised the people documenting this codebase. Nothing here is a hypothetical: each item was found while reading
the source or running it. The documentation describes the code **as it is**; none of these were changed while writing it.

**Confidence markers**

* **[run]** reproduced by executing the real function while writing these docs.
* **[read]** derived from reading the code; not executed.
* Source references are `path:line` at the time of writing; use the function name if the line has moved.

## Highest impact for operators

| # | Issue | Why it matters | Evidence |
|---|---|---|---|
| 1 | **Cards show a different score after a button tap.** `bot._handle_callback` calls `_guest_dicts([row], now=now)` without a profile, so re-rendered cards show the raw LLM score, while digest and urgent cards show the blended quality score. | The number on a card changes when you tap it. | [read] `src/jobdigest/bot.py` (`_handle_callback`) |
| 2 | **`last_digest_job_ids` can return urgent jobs.** The urgent slot also writes a `runs` row of kind `digest` with `job_ids`, and `contacts` reads the latest OK `digest` run. | The contacts step may enrich the urgent run's jobs instead of the morning digest's. | [run] `src/jobdigest/db/runs.py:105`, `src/jobdigest/digest.py` (`_run_urgent`) |
| 3 | **A crashed `collect` is recorded as `ok`.** `status` stays `"ok"` until the end; if an exception escapes (anything other than the per-unit isolation), `finally` calls `finish_run(..., "ok")` and the exception then propagates. | `collect_is_ok` could consider a crashed run healthy for up to 26 hours. | [read] `src/jobdigest/collect.py:564-591` |
| 4 | **An empty "ok" result tombstones a source.** Feeds, marketplaces and (via their own convention) portals sweep with `success=True`. A silently broken parser that returns zero jobs makes every previous job of that source miss, and after two runs they are closed. Pinned by `tests/test_tombstone.py::test_empty_rss_two_ok_fetches_closes`. | Silent data loss from a selector drift. | [read] `src/jobdigest/collect.py:364` |
| 5 | **The discovered-board list is untracked.** `data/boards_india.tsv` exists locally but is not committed, so hosted runs do not see those boards until it is. | The hosted pipeline fetches only curated boards. | `git status` |
| 6 | **Without `DATABASE_URL` the bot cannot work**, and the pipeline relies on an Actions cache for memory. `bot.yml` refuses to run without the secret; the cache is not visible to the bot. | Button taps need the shared database. | `.github/workflows/bot.yml`, `pipeline.yml` |
| 7 | **Only `httpx.HTTPError` is caught around a board fetch.** Any other exception in `fetch_fn` or in `upsert_job` (for example an invalid regex in the profile) aborts the whole collect, unlike portals/feeds, which catch everything per unit. | One bad record can stop all collection. | [read] `src/jobdigest/collect.py:413` |
| 8 | **A run where every board is `DEAD_TOKEN` or `EMPTY_BOARD` exits 0.** Those statuses are not error units. | Broken tokens do not trigger the loud alert. | [read] `src/jobdigest/collect.py:268-282` |

## Matching and scoring heuristics

| Issue | Evidence |
|---|---|
| **`is_top_company` matches single words anywhere in the name.** "Pizza Slice Inc" matches `slice`, "Cred Club" matches `cred`; generic entries (`slice`, `ola`, `navi`, `cred`, `amd`) can produce false positives (which loosen the frontend/QA gate and boost fit). | [run] `src/jobdigest/tiers.py:40` |
| **Years-of-experience regexes over-match.** `_RANGE` has no "ago" guard (`3 to 5 years ago` counts); `_MIN` has no leading word boundary (`admin 2 years` counts). | [run] `src/jobdigest/hard_filter.py:8,13` |
| **"Hardware Engineer Intern" passes** because "engineer" is treated as a strong engineering word. | [run] `src/jobdigest/hard_filter.py` (`function_ok`) |
| **The India check ignores the description when a location is present.** | [read] `src/jobdigest/hard_filter.py:176` |
| **`Profile.timezone` is never used.** IST is hard-coded for follow-ups and urgent-day accounting; scheduling is UTC cron. | [read] `src/jobdigest/config.py:113`, `src/jobdigest/followups.py:6` |
| **Company-name normalisation can merge different companies.** "X Private Limited" -> "X Private", "X Software" -> "X"; `_names_differ` treats Meta and Metabase as the same. | [run] `src/jobdigest/normalize.py:49`, `src/jobdigest/legitimacy.py:124` |
| **`posting_key` can be `None`** (empty title or company), so such a job is never linked as a duplicate. | [run] `src/jobdigest/normalize.py`, `src/jobdigest/db/jobs.py:281` |
| **A tombstoned canonical row keeps hiding its alias.** `link_duplicate_jobs` also ranks inactive rows. | [read] `src/jobdigest/db/jobs.py:141` |
| **`_score_item` defaults a missing `stack_ok` to false**, which makes the job irrelevant. | [read] `src/jobdigest/score.py:122` |
| **If the omitted-job retry fails, no batch failure is counted**; the run can exit 0 with jobs left unscored until the next run. | [read] `src/jobdigest/score.py:267` |
| **`blurb._cite_ok` accepts any substring** of an internship, not a verbatim cite. | [read] `src/jobdigest/blurb.py:134` |

## Contacts and email guesses

| Issue | Evidence |
|---|---|
| **Pattern inference cannot tell `john`, `johns` and `jsmith` apart:** all infer `{fi}{last}`; the `{first}{li}` rule is unreachable. | [run] `src/jobdigest/contacts/pattern.py:16-21` |
| **JD contacts are merged first**, so a GitHub contact's observed email is dropped when the JD record already carries a constructed one. | [read] `src/jobdigest/contacts/rank.py:62-74` |
| **A single-word name with `{first}.{last}` yields `madonna.@acme.com`** and passes the validity regex. | [read] `src/jobdigest/contacts/email.py:23` |
| **Domain verification is a substring test** with no minimum slug length; Clearbit's first hit is trusted. | [read] `src/jobdigest/contacts/domain.py:43-46` |
| **The catch-all in `run_pipeline` returns `[]`**, discarding even JD names after any bug, and is not counted as an error. | [read] `src/jobdigest/contacts/pipeline.py:71-73` |
| **`email_inferred` is not persisted**; after storage only the confidence cap of 40 marks a guessed email. | [read] `src/jobdigest/contacts/types.py`, `db/contacts.py` |
| **`connect` and `insert_run` are outside the `try` in `contacts.run`**, so a database failure raises instead of returning 1. | [read] `src/jobdigest/contacts/run.py:89-90` |

## Delivery and the bot

| Issue | Evidence |
|---|---|
| **The pager is sent after jobs are stamped notified.** If it fails the run returns 1 but the jobs stay stamped; and `finish_run("ok")` happens before delivery, so a send failure shows only in the exit code. | [read] `src/jobdigest/digest.py` (`_deliver_digest`, `_run_digest`) |
| **`bot_post` checks only the HTTP status**; a 200 response with `"ok": false` is not detected. | [read] `src/jobdigest/telegram.py` |
| **`run_for` can re-deliver the last poll's updates** (they are not acknowledged before the deadline), so Why me or Mail may be answered twice. | [read] `src/jobdigest/bot.py` (`run_for`) |
| **Without a bot token the poll loop spins** until the deadline (nothing sleeps). | [read] `src/jobdigest/bot.py` (`poll_once`, `run_for`) |
| **`apply_bot_status` protects only `oa`, `interview`, `offer`**; a tap can overwrite `rejected` or `ghosted`. | [read] `src/jobdigest/db/applications.py:233` |
| **`ensure_headers` overwrites an unrecognised row 1** of the sheet; the sheet's Status/Applied/Notes edits are never read back. | [read] `src/jobdigest/sheet.py` |
| **`health.collect_health` hides a `dead_token` with a streak below 2.** | [read] `src/jobdigest/health.py` |

## Sources

| Issue | Evidence |
|---|---|
| **Unknown ATS names fall through to Workable.** `get_adapter` ends with an unconditional `return WorkableAdapter(...)`; a key added to `KNOWN_ATS` without a branch silently gets the wrong adapter. | [read] `src/jobdigest/ats/__init__.py:38` |
| **Lever has no page cap** and does not dedupe across pages. | [read] `src/jobdigest/ats/lever.py:22,33` |
| **SmartRecruiters and (mostly) BambooHR jobs have no description text**, so the filters and the LLM see only the title. | [read] `src/jobdigest/ats/smartrecruiters.py`, `bamboohr.py` |
| **Greenhouse `posted_at` is `updated_at`**, not the first publish date. | [read] `src/jobdigest/ats/greenhouse.py` |
| **`decode_html_content` is a regex strip**, not an HTML parser (`x < 5 and y > 3` loses text). | [read] `src/jobdigest/ats/base.py:70` |
| **`is_challenge` labels any 403/429/503 as Cloudflare**, and `run_one` fetches robots.txt with plain `httpx` even in a Scrapling session. | [run] `src/jobdigest/portals/session.py:47`, `portals/__init__.py:244` |
| **Portal cards from three parsers have empty descriptions** (whitespace-only direct text is truthy). | [run] `src/jobdigest/portals/generic.py:74` |
| **`normalize_url` is not safe for scheme-less URLs:** `example.com/jobs` becomes `https:///example.com/jobs`. | [run] `src/jobdigest/normalize.py:92` |
| **`expand.probe` drops a board when `Retry-After` is non-numeric.** | [read] `src/jobdigest/expand.py:130` |

## Storage

| Issue | Evidence |
|---|---|
| **The Postgres shim does not translate SQLite-only syntax**; `INSERT OR IGNORE` into an id table would be given `RETURNING id` and fail. Nothing issues such a statement today. | [read] `src/jobdigest/db/connection.py:79` |
| **`split_sql` splits on every `;`**, breaking on a semicolon inside a literal (none in the schema). | [read] `src/jobdigest/db/connection.py:105` |
| **A database missing base columns cannot be repaired**; only `ADDED_COLUMNS` are migrated. | [read] `src/jobdigest/db/schema.py:323` |
| **The `applications` table rebuild is not atomic as a whole** and hard-codes its column list. | [read] `src/jobdigest/db/schema.py:244` |
| **A UPDATE can raise `IntegrityError`** when a URL match carries an `external_id` that collides on `jobs_source_ext`. | [read] `src/jobdigest/db/jobs.py:429` |
| **`collect_is_ok` uses the real clock**, not the patchable `now()`. | [read] `src/jobdigest/db/runs.py:75` |

## Configuration, CI and tests

| Issue | Evidence |
|---|---|
| **A missing config file silently becomes the example.** The fallback logs a warning but the run continues. | `src/jobdigest/config.py` (`_yaml_path`) |
| **`bool(d["reject_remote_us"])` turns the string `"false"` into `True`**, and `float(d.get("digest_min_quality"))` raises on a YAML null. | [read] `src/jobdigest/config.py:111,120` |
| **`cron` treats `N/step` as the single value `N`** (not standard cron) and rejects names like `MON`. | [run] `src/jobdigest/cron.py:18` |
| **Missing cron windows lose a day.** A tick delayed by more than 15 minutes skips that day's digest. | design of `cron.is_due` |
| **`pip install -e .` followed by `.[marketplaces]` is redundant**, and the Chromium cache key hard-codes `1.62.0`. | `.github/workflows/pipeline.yml` |
| **README drift:** mentions `config/companies.hosted.yaml` (does not exist); omits the SQLite state cache and `bot.yml`. | `README.md` |
| **Tracked config differs from production:** `models.yaml`, `portals.yaml`, `feeds.yaml` are committed although `config/*.yaml` is ignored; tests that call `load_models()` read the tracked file, and tests rely on `profile.yaml` being absent. | `.gitignore`, `tests/` |
| **Unused or untested:** `config.load_all`, `db.jobs.note_seen_job` (production), fixtures `ats/ashby/*` and `ats/workable/*`, `URGENT_MIN_FIT`, the quality-blended card score, `openrouter.chat`'s HTTP path, `robots.py`, `logutil`, and the research scripts. Postgres is never exercised live. | `src/`, `tests/` |

## Research scripts and minor items

| Issue | Evidence |
|---|---|
| **The Workable "productish" regex contains `AI` with `re.I`**, so it matches "Retail" and keeps unrelated candidates. | [run] `scripts/probe_ats_tokens.py:730-733` |
| **`confidence(jobs, True)` always passes `notable=True`**, so confidence is never "low" (also in wave 2). | [read] `scripts/probe_ats_tokens.py`, `scripts/probe_ats_tokens_wave2.py` |
| **`probe_ashby` and `probe_workable` collapse every error, including 429, to `None`**, so rate-limited boards are silently dropped; only `probe_lever` reports `ok: False`. | [read] `scripts/probe_ats_tokens.py:587-645` |
| **`_try_parse_dt` reads a bare number as epoch seconds** (`20261030` becomes 1970-08-23) and returns `None` for a numeric string. | [run] `src/jobdigest/legitimacy.py:62` |
| **`norm_http_url` does no host validation** (`https:///nohost` passes through). | [run] `src/jobdigest/db/_common.py:37` |
| **After an injected `RETURNING id`, the row is already fetched**, so a later `cur.fetchone()` on that cursor returns nothing. | [read] `src/jobdigest/db/connection.py:113` |
| **`cli._cmd_collect` mixes direct and `getattr` flag access** (inconsistent defensive style). | [read] `src/jobdigest/cli.py:12-21` |

## How to use this list

Treat items 1-8 as the first candidates for fixes. When you fix one, delete or amend its row here in the same change, and update the
module page it points to. See [adding features](../onboarding/06-adding-features.md) for the workflow.
