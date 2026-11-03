# Glossary

Terms as this codebase uses them, with a pointer to where each is implemented or explained.

| Term | Meaning | See |
|---|---|---|
| **ATS** | Applicant tracking system: the hosted careers page a company uses (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, BambooHR). We read its public JSON. | [ats-overview](../modules/sources/ats-overview.md) |
| **Board** | One company's job list at one ATS, identified by `(ats, token)`. | [collect-run](../workflows/collect-run.md) |
| **Token** | The company slug in a board's URL (for example `stripe` in `boards-api.greenhouse.io/v1/boards/stripe/jobs`). | [expand](../modules/sources/expand.md) |
| **Discovered boards** | Boards found by `jobdigest.expand`, stored in `data/boards_india.tsv` and merged into `collect`. | [board-discovery](../workflows/board-discovery.md) |
| **Lane** | A kind of source in `collect`: ATS, portals, feeds, marketplaces. | [collect-orchestrator](../modules/collection/collect-orchestrator.md) |
| **Unit** | One board/portal/feed/marketplace row processed and counted by `collect` (`enabled_units`, `error_units`). | [collect-orchestrator](../modules/collection/collect-orchestrator.md#_collector) |
| **`NormalizedJob`** | The single record type every source produces: title, company, location, URLs, description, `external_id`, `posted_at`, ... | [ats-base-and-http-client](../modules/sources/ats-base-and-http-client.md) |
| **`FetchResult` / `FetchStatus`** | An adapter's reply: status `OK`, `EMPTY_BOARD`, `DEAD_TOKEN` or `ERROR`, jobs, and the `complete` flag. | [ats-overview](../modules/sources/ats-overview.md#the-contract-fetchresult) |
| **`complete`** | True when a fetch saw the whole board; only complete fetches may close vanished jobs. | [data-model](../architecture/data-model.md) |
| **Dead token** | A board that returned 404/410 or redirected; counted in `dead_streak`. | [collect-run](../workflows/collect-run.md) |
| **Tombstone** | Closing a job (`is_active = 0`, `close_reason = 'unseen_board'`) after two complete fetches without it. | [storage/jobs](../modules/storage/jobs.md#bump_unseen_for_source) |
| **Hard filter** | Regex gates in `hard_filter.py` that set `hard_status` (`pass`/`reject`) and `hard_reason`. | [hard-filter](../modules/filtering/hard-filter.md) |
| **Function gate / level gate / seniority gate** | The code-level checks for engineering role, entry-level evidence, and experienced titles. | [hard-filter](../modules/filtering/hard-filter.md) |
| **Legitimacy flag** | `suspect` marker from structural checks (company mismatch, no responsibilities, repost, age). | [legitimacy](../modules/filtering/legitimacy.md) |
| **`apply_url_norm`** | The normalized apply URL that uniquely identifies a job. | [normalize](../modules/foundation/normalize.md) |
| **Posting key** | Hash of normalized title + company + location, used to detect the same role on two sources. | [normalize](../modules/foundation/normalize.md) |
| **Canonical job** | The one row kept per posting key (`canonical_job_id IS NULL`); duplicates point at it. | [storage/jobs](../modules/storage/jobs.md#link_duplicate_jobs) |
| **Upsert** | Insert a new row or update the existing one; `upsert_job` is the central write. | [storage/jobs](../modules/storage/jobs.md#upsert_job) |
| **Score (LLM score)** | The model's 0-100 verdict stored in `scores` for a `(model, prompt_version)`. | [score-llm](../modules/ranking/score-llm.md) |
| **Coerce** | Overriding the model's answer with deterministic rules (`coerce_relevant`). | [score-llm](../modules/ranking/score-llm.md#coerce_relevant) |
| **Stale score** | A score marked `stale = 1` because the job's description changed. | [data-model](../architecture/data-model.md) |
| **Fit** | Deterministic 0-100 score: role family (30) + entry level (20) + skills (25) + trust (15) + freshness (10). | [fit-and-tiers](../modules/ranking/fit-and-tiers.md) |
| **Quality** | `0.65 * fit + 0.35 * LLM score`; shown on digest cards. | [fit-and-tiers](../modules/ranking/fit-and-tiers.md#quality_score) |
| **Blend rank** | Quality + location bonus + company prior; the sort key. | [rank-and-selection](../modules/ranking/rank-and-selection.md#blend_rank) |
| **Top-tier company** | A name in `tiers.TOP_COMPANIES` or the profile's `top_companies`. | [fit-and-tiers](../modules/ranking/fit-and-tiers.md) |
| **Company prior** | Small ranking bonus (max 5) for companies where earlier applications reached OA/interview/offer. | [rank-and-selection](../modules/ranking/rank-and-selection.md) |
| **Digest** | The Telegram message of ranked cards sent once per day. | [scoring-and-digest](../workflows/scoring-and-digest.md) |
| **Guest / guest row** | A job selected for a digest. The code uses "guest" for "digest candidate". | [digest](../modules/delivery/digest.md) |
| **Card** | One job's text block: `N. Company - Title | location | score | age | source | url` plus a "Why you" line. | [summarize](../modules/ranking/summarize.md#render_card) |
| **Pager** | The message with numbered buttons that opens a job's action card. | [telegram](../modules/delivery/telegram.md) |
| **Action card** | A card with buttons Apply / Save / Applied / Hide / Why me? / Mail. | [telegram](../modules/delivery/telegram.md) |
| **Callback data** | The `<kind>:<job_id>` string behind a button (`o`, `s`, `a`, `h`, `w`, `m`). | [telegram](../modules/delivery/telegram.md#callback-data-protocol) |
| **Slot** | Which scheduled send is due: `digest` or `urgent`. | [cron-gate](../modules/foundation/cron-gate.md) |
| **Gate job** | The cheap first job of `pipeline.yml` that decides whether the heavy job runs. | [deployment](../architecture/deployment-and-scheduling.md) |
| **Urgent alert** | A weekday card for a job with LLM score >= `urgent_score_min` and fit >= 70 (or watchlisted). | [digest](../modules/delivery/digest.md#maybe_send_urgent) |
| **Cooldown** | A company sent within `digest_company_cooldown_days` is skipped unless quality >= 85. | [rank-and-selection](../modules/ranking/rank-and-selection.md#select_diverse) |
| **Loud failure** | Pushing a `... FAILED` message to Telegram and exiting 1. | [error handling](../architecture/error-handling-and-observability.md) |
| **Injection / seam** | Passing a collaborator (HTTP client, LLM function, Telegram poster) as an argument so tests can fake it. | [testing strategy](../testing/strategy.md) |
| **State cache** | The Actions cache that carries the SQLite file between pipeline runs when there is no Postgres. | [deployment](../architecture/deployment-and-scheduling.md) |
| **Long polling** | Calling Telegram's `getUpdates` with a timeout so it replies as soon as an update exists. | [telegram-button-tap](../workflows/telegram-button-tap.md) |
| **Slot window** | The 15-minute window `cron.is_due` checks for each tick. | [cron-gate](../modules/foundation/cron-gate.md) |
