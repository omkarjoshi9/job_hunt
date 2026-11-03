# Test map

Every `tests/test_*.py` file, the source module(s) it covers, how many pytest items it collects, and what it pins. Counts are collected items (a parametrized test counts once per parameter); total 732 collected, 731 passed, 1 skipped (checked 2026-11-03). Strategy and seams: [strategy.md](strategy.md). Fixtures: [e2e-and-fixtures.md](e2e-and-fixtures.md).

Paths below are relative to `src/jobdigest/`.

## Sources: ATS adapters and HTTP

| Test file | Covers | N | What it pins |
|---|---|---|---|
| `test_ats_greenhouse.py` | `ats/greenhouse.py`, `ats/base.py` | 13 | two-job OK parse; empty board; 404 is dead token (not empty); double-encoded HTML description decoded; 429 then 200 calls `sleep`; 429 after retry is ERROR; 403 is ERROR not dead token; office parent name not in location; 200 HTML challenge is ERROR; `meta.total` mismatch handling (duplicate page marks incomplete, second page appends); page-2 HTTP error keeps page 1 and incomplete; max pages marks incomplete |
| `test_ats_lever.py` | `ats/lever.py` | 5 | two pages give three jobs (`skip` offset paging); empty; 404; partial pagination failure is incomplete but OK; `complete` true on full fetch |
| `test_ats_ashby.py` | `ats/ashby.py` | 6 | no pagination params sent; two jobs; unlisted job excluded; empty; 404; invalid JSON structure is ERROR (payloads inline, fixtures unused) |
| `test_ats_workable.py` | `ats/workable.py` | 7 | no pagination params; two jobs; company name from envelope; empty; 404; bad structure; `telecommuting` true falls back to remote hint (inline payloads) |
| `test_ats_smartrecruiters.py` | `ats/smartrecruiters.py` | 7 | trims title and https apply URL; empty; 404; pagination concatenates without duplicate ids; invalid JSON; HTML challenge; max pages incomplete |
| `test_ats_bamboohr.py` | `ats/bamboohr.py` | 6 | trims title and https apply; empty; 404 not empty_board; invalid JSON; 200 HTML login is ERROR; single GET without pagination params |
| `test_ats_posted_at.py` | `ats/base.py` (`parse_posted_at`, posted-at helper) | 7 | epoch seconds and millis, ISO Z, ISO with millis, date-only, bool/empty rejected, first non-empty key |
| `test_ats_status.py` | `ats/base.py` | 6 | HTTP status classification (404/410/redirect dead token; 200 parse body; 403/429/5xx error), `empty_or_ok`, double HTML unescape, `join_locations` dedupe |
| `test_ats_registry.py` | `ats/__init__.py`, `collect.py` (`_board_url`) | 5 | `get_adapter` for smartrecruiters and bamboohr; workday/getro still raise unimplemented; collect knows their board URLs |
| `test_http_client.py` | `ats/http_client.py` | 11 | retry policy: no retry on OK; 429 retried once honouring `Retry-After` (seconds or HTTP date, garbage and cap); 5xx retried twice then gives up; 4xx not retried; timeout retried then raises; other errors not retried |

## Sources: portals, feeds, marketplaces, discovery

| Test file | Covers | N | What it pins |
|---|---|---|---|
| `test_portals_parsers.py` | `portals/__init__.py`, `foss_united.py`, `null_community.py`, `hasjob.py`, `generic.py` | 23 | parser output on fixture HTML/JSON (including live-layout variants); hasjob JSON payload shapes; challenge detection; `run_one` error classes (unknown fetch, unknown parser, bundle error is http); `Accept: application/json` only for hasjob; injected `get_html` for dynamic/stealth |
| `test_portal_session.py` | `portals/session.py` | 10 | fetch mode defaults to http; `get` vs `fetch` per mode; same-host politeness `sleep`; `solve_cloudflare` for stealth; header merging; empty bundle opens no sessions; mode errors recorded |
| `test_feeds.py` | `feeds.py` | 7 | two RSS jobs each fixture; module stays httpx + feedparser; `posted_at` ISO Z; timeout classified by type; empty feed is parse error; OK path |
| `test_marketplaces_bridge.py` | `marketplaces/__init__.py`, `jobspy_bridge.py` | 10 (1 skipped) | row to `NormalizedJob` mapping; skip untitled/identity-less rows; naukri defaults `linkedin_fetch_description`; unknown site is parse error; ImportError is config error; empty scrape OK; proxies forwarded; `posted_at` normalisation (skipped without pandas) |
| `test_discover.py` | `discover.py` | 15 | India filter; ATS token extraction (greenhouse, lever, ashby dot token); workday skipped; `gh_jid` without board unresolved; diff known vs new; suggested YAML has `enabled: false`; never writes companies path; probe overlay uses new ids only; `--url` needs fetch; probe failure restores config env; no discover in workflows |
| `test_probe.py` | `probe.py` | 8 | status mapping via mock transport and no YAML writes; all seed companies disabled; `--id` filtering (repeatable, intersects `--enabled-only`); smartrecruiters/bamboohr not "unimplemented" |
| `test_expand.py` | `expand.py` | 4 | `promising` screens title and location; probe per ATS shape and dead boards; keeps only boards with hits and TSV round trip; `read_boards` dedupes and skips unsupported ATS |

## Collection orchestration and storage

| Test file | Covers | N | What it pins |
|---|---|---|---|
| `test_collect.py` | `collect.py` | 9 | upsert twice no doubles; all-error run fails (exit 1); default YAML has no enabled; disabled not fetched; failure sends Telegram alert; workplace hint persisted; resolved URL written on ok/empty/dead/error |
| `test_collect_portals.py` | `collect.py`, `cli.py`, `portals/` | 9 | portal and feed URL dedupe; `--ats-only`, `--skip-*` flags; git-tracked YAML portals/feeds; `--portal-fetch http` skips dynamic rows |
| `test_collect_marketplaces.py` | `collect.py`, `marketplaces/` | 4 | marketplace upsert and sweep; `--ats-only` skips marketplaces; `--skip-marketplaces`; tracked YAML marketplaces disabled |
| `test_collect_no_llm.py` | source text of `collect.py`, `score.py`, `digest.py`, `bot.py`, `blurb.py` | 2 | forbidden imports (see [strategy.md](strategy.md#teststest_collect_no_llmpy-2-tests)) |
| `test_tombstone.py` | `collect.py` sweep, `db/jobs.py`, `db/schema.py` | 14 | two successful absences tombstone; empty board closes; error / dead token / incomplete OK never count a miss; reopen resets miss count keeping `first_seen`; tombstoned job leaves guest rows; portal zero cards does not bump; empty RSS twice closes; per-board isolation; disabled company not swept; source frozen at first insert |
| `test_health.py` | `health.py`, `collect.py` | 6 | classification (error, empty, zero, dead); dead token needs streak 2; healthy format; dead-streak increment/reset; footer printed when unhealthy; error does not bump miss count |
| `test_db_upsert.py` | `db/jobs.py` | 8 | foreign keys on; UTM variants are one row; URL-less empty id no collision; employment/workplace hints persisted, overwritten with NULL, capped at 80 chars; RSS and portal same URL one row; migration adds hint columns |
| `test_db_internals.py` | `db/connection.py`, `db/schema.py`, `paths.py` | 8 | `repo_root` re-exported; `_pg_sql` literal and RETURNING rules; `_safe_ident`; transaction commit/rollback; `upsert_job` single commit; new indexes; legacy `jobs` table migrates |
| `test_db_postgres.py` | `db/connection.py`, `db/schema.py` | 4 | path ignores `DATABASE_URL`; `uses_postgres`; `SCHEMA_PG` identity rules; placeholder/RETURNING translation (no live Postgres) |
| `test_db_contacts.py` | `db/contacts.py` | 11 | insert; dedupe on job+name+source; higher confidence wins; blank name / invalid source skipped; `contacts_for_job`, `best_contact` preference; SQLite path ignores `DATABASE_URL`; schema migration creates table; `last_digest_job_ids` |
| `test_content_hash.py` | `normalize.py`, `db/jobs.py` | 8 | description hash whitespace collapse; migration adds hash and stale; changed JD marks scores stale; unchanged does not; first populate does not stale; stale jobs rescored; stale excluded from guest rows; `upsert_score` clears stale |
| `test_dual_hash.py` | `normalize.py` (`posting_key`), `db/` | 8 | alias vs canonical company keys; ATS + portal duplicates are one guest, ATS wins regardless of insert order; agencies not merged; application on alias suppresses canonical; unscored skips aliases; board error does not bump miss |
| `test_normalize_text.py` | `normalize.py`, `db/jobs.py`, `hard_filter.py` | 8 | company legal-suffix, ampersand, "Google Cloud India" distinct; title normalisation; location alias; raw plus normalised stored; hard filter uses raw campus title |
| `test_normalize_url.py` | `normalize.py` | 5 | host case, trailing slash, utm/gclid/www stripping, fragment dropped, `job_identity` rules |
| `test_envfile.py` | `envfile.py` | 2 | export/comment/quote parsing; existing env not overridden |
| `test_config.py` | `config.py` | 13 | profile types; missing `github_url` defaults; no digest hour; example profile anonymous; models not `:free`; prompt version non-empty; blurb model paid and distinct; model without blurb block; marketplaces loader; `batch_size` under `score` and legacy top-level; example fallback logs a warning |
| `test_cron.py` | `cron.py` | 23 | defaults (`30 3 * * *`); due at slot, inside delayed slot, not at neighbours; empty cron uses default; dispatch always due; slot floor to 15 minutes; `main` writes `GITHUB_OUTPUT` (due, slot) and exits 1 on bad cron; stdlib-only; urgent slot (weekday 08:30 UTC), Saturday not, Sunday digest, `off`, empty uses default, both match prefers digest; DOM/DOW OR semantics |
| `test_cli.py` | `cli.py` | 2 | `contacts` flags (`--db --limit --job-id` repeatable) and defaults |

## Filtering and ranking

| Test file | Covers | N | What it pins |
|---|---|---|---|
| `test_hard_filter.py` | `hard_filter.py` | 87 | the regex gate: title reject (staff, senior, manager, director, VP, head of) and its non-matches (tech leadership, fellowship, directory, head office); remote-US reject; India keyword detection (Delhi NCR yes, "increase" and Indiana no); years-of-experience ranges and edge forms (en dash, "to", "yrs", "plus"; 1-3, 2-3 reject, 0-5 and "minimum two" cases); non-engineering interns, ops associates, campus/university recruiters rejected; parametrized good/bad title lists; frontend/QA only at top companies |
| `test_legitimacy.py` | `legitimacy.py`, `rank.py`, `digest.py` | 17 | 90-day age flag boundary (89 vs 91); tombstoned not guest; company mismatch (agency, legal suffix, portals without company id); no-responsibilities heuristic; US language in first 2000 chars hard reject vs later chip; JD repost by hash; alias is not repost; evaluation does not change score/relevant; blend rank unchanged by suspect flag; chip empty when OK and max 24 chars |
| `test_fit.py` | `fit.py`, `rank.py`, `score.py`, `hard_filter.py`, `legitimacy.py` | 37 | quality = fit + LLM blend (same LLM score no longer same rank); new-grad above intern; core engineering above frontend at top company; top company and direct ATS boosts; `sort_rows` by quality; `select_diverse` quality bar, caps, no padding; company mismatch variants (parametrized); junk and good titles (parametrized); score retries jobs the model omitted; one role per company; company cooldown; stale posts not new; digest does not resend across days; 0-2 years is not entry-level evidence; company name variants count as one |
| `test_rank.py` | `rank.py` | 31 | `recency_boost` (0h max, 4h, 6d, 7d zero, blank/unparseable zero, naive ISO as UTC, future clamped); `location_pref` (home, relocate, bangalore alias, title fallback, home beats relocate, ignores description, word boundary); blend examples A, B, C; id tie-break; `age_chip`; `source_chip`; LLM lead beats max boost; prior can flip a 5-point gap |
| `test_priors.py` | `rank.py`, `db/digest_queries.py`, `normalize.py` | 17 | company prior (OA +5, interview/offer do not stack, saved/applied/hidden/rejected/ghosted zero, missing zero); prior capped; recency outranks prior; priors gated below 20 outcomes; guest rows ordering with prior; prior never surfaces a hard reject; applied URL still excluded; company normalisation; portal job without company id |
| `test_score.py` | `score.py` | 17 | persistence of relevant/score; unknown job id dropped; OpenRouter error; fenced prompt omits raw JSON; commitment hint in fence (capped 80); system prompt rules and both few-shot anchors within token budget; calibration ids dropped; model "yes" overridden for ops associate, stack_ok false, senior SWE, YOE in fence; `_coerce_relevant` |
| `test_summarize.py` | `summarize.py` | 14 | invented ids dropped; missing urls appended; fallback on error/ValueError; empty guest; fallback bullets; `render_card` is two lines, chip placement, empty location slot, newline collapse, gap location; prompt version in system message |
| `test_blurb.py` | `blurb.py` | 7 | prompt omits name and CGPA; citation must match an internship; ungrounded CamelCase flagged; no internships means no model call; `json_mode` false; word-count flag does not re-call; OpenRouter error returns refusal |

## Delivery

| Test file | Covers | N | What it pins |
|---|---|---|---|
| `test_digest.py` | `digest.py`, `db/digest_queries.py` | 8 | no collect run fails with no chat; score failure skips summarizer; three relevant not junk; zero pass silent; `_guest_dicts` keeps why/chips, gap location, age overlay; digest does not import blurb |
| `test_digest_telegram.py` | `digest.py`, `telegram.py` | 6 | score-failed alert; collect-failed alert; empty guest sends nothing; non-empty body; why text; twelve guests split without straddling cards |
| `test_urgent.py` | `digest.py` urgent path, `db/` | 10 | watchlist exact casefold; threshold 85; watchlist bypasses threshold; 36 h lookback; daily cap 3 in IST; mute does not stamp; stamp after real send; morning rows skip notified; `relevant_unapplied_jobs` still sees notified; urgent-only sends no pager |
| `test_telegram.py` | `telegram.py` | 18 | message splitting (short, long block, two-line cards); plain text no parse mode; skip empty; require-flag semantics; HTTP error returns False; URL only in this module; callback data fits 64 bytes (o, w, m); pager and action card markup; `getUpdates` allowed updates; why-me row |
| `test_bot.py` | `bot.py`, `db/applications.py` | 35 | `apply_bot_status` (applied excludes guest, hide, save, preserves OA notes, `applied_on` restamp rules); `handle_update` per command and callback (open, applied, why-me with refusals for reject/inactive/hidden, mail, slash commands, wrong chat silent); `poll_once`; negative mail cache and expiry; lookup failure not cached; unknown callback; missing job; slow action exception |
| `test_bot_buttons.py` | `bot.py`, `.github/workflows/bot.yml` | 30 | contract that each of six buttons (o, s, a, h, w, m) is answered exactly once and leaves a visible message in four failure modes; unknown button; status buttons change card and DB; `drain` terminates; `run_for` honours deadline; bot workflow structure |
| `test_sheet.py` | `sheet.py`, `db/applications.py`, `db/digest_queries.py` | 32 | `FakeSheet` based; append idempotence, UTM pair, reconnect; `upsert_application` rules; guest/rows ordering by blend; missing credentials skip vs exit 1; header migration and layout/validation; hand-edited status kept; on-campus URL skipped; blend order of appended rows |
| `test_followups.py` | `followups.py` | 7 | IST business-day counting; due after 7 business days, excludes saved/hidden/OA, caps at five oldest; empty formatting |
| `test_email_draft.py` | `email_draft.py`, `templates/email/*` | 4 | recruiter vs cold template choice; empty GitHub line omitted; no email means no mailto; deterministic fallback observation |

## Contacts

| Test file | Covers | N | What it pins |
|---|---|---|---|
| `test_contacts_domain.py` | `contacts/domain.py` | 6 | Clearbit hit; probe fallback; empty company; rejects redirect to unrelated host; accepts www same slug; network error falls back |
| `test_contacts_email_pattern.py` | `contacts/email.py`, `pattern.py` | 7 | diacritics/punctuation stripping; rejects empty/bad pattern; two votes needed; detection fallback paths |
| `test_contacts_github.py` | `contacts/github.py`, `email.py`, `pattern.py` | 5 | first.last construction; Actions token ignored; pattern from commits; recruiter filter; 403 empty |
| `test_contacts_jd.py` | `contacts/jd.py` | 5 | hiring manager parse; recruiter and degree false-positive; empty; job-title lookalikes; real name next to title words |
| `test_contacts_pipeline.py` | `contacts/pipeline.py`, `run.py`, `rank.py`, `types.py` | 8 | pipeline never raises and merges; swallows HTTP errors; constructed email attaches to JD contact; banned-integrations source scan; `run` uses job ids; `enrich_job` reads description; confidence capping rules |

## Constraint and end-to-end

| Test file | Covers | N | What it pins |
|---|---|---|---|
| `test_scaffold_constraints.py` | `pyproject.toml`, `.env.example`, `README.md` | 6 | see [strategy.md](strategy.md#teststest_scaffold_constraintspy-6-tests) |
| `test_no_stealthy.py` | all of `src/jobdigest` | 2 | Scrapling session classes only in `portals/` |
| `test_workflows.py` | `.github/workflows/*`, `start.sh` | 8 | see [strategy.md](strategy.md#teststest_workflowspy-8-tests) |
| `test_pipeline_e2e.py` | `collect`, `digest`, `score`, `contacts.run`, `sheet`, `cli`, `pipeline.yml` | 14 | full collect, digest, contacts, sheet-sync run; see [e2e-and-fixtures.md](e2e-and-fixtures.md) |

## Source modules with no dedicated test file

Checked by grepping `tests/` for imports of or references to the module: no test mentions `logutil`, `tiers` or `robots` by name (`tiers.py` is exercised only indirectly: `fit.py:16` and `hard_filter.py:6` import `is_top_company` from it). `openrouter.py` is imported by tests only for `OpenRouterError` and the `chat_fn` signature; `openrouter.chat` itself (the real HTTP call) is never exercised. `paths.py` has one assertion in `test_db_internals.py`; `db/runs.py` and `db/applications.py` are exercised through stage tests rather than their own files; `__main__.py` has none.
