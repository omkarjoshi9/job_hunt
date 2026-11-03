# Configuration files, data files and environment

- Source: `config/*.yaml` (9 files), `data/*.tsv` (4 files), `.env.example`, `.gitignore`, `src/jobdigest/templates/email/*`, loaders in `src/jobdigest/config.py`
- Related: [github-workflows.md](github-workflows.md) (secrets that become these files), [../modules/foundation/config.md](../modules/foundation/config.md) (loader internals), [../modules/foundation/paths-envfile-logutil.md](../modules/foundation/paths-envfile-logutil.md)

## How configuration is found

`src/jobdigest/config.py` resolves a YAML file by name:

1. `config_dir()` (line 16) returns `$JOBDIGEST_CONFIG_DIR` if set (expanded and resolved), otherwise `<repo>/config`.
2. `_yaml_path(name)` (line 23) returns `config/<name>` if it exists; else `config/<stem>.example.yaml` with a logged warning (`"%s not found; falling back to example config %s"`); else raises `FileNotFoundError`.
3. `_read_yaml` (line 34) parses with `yaml.safe_load`; an empty document raises `ValueError("empty yaml: ...")`.

So a fresh clone runs on the example files. That is deliberate: tests and a first `collect` work with no private setup (all example rows are `enabled: false`, so nothing is fetched).

## Tracked versus private

`.gitignore` contains `config/*.yaml` followed by `!config/*.example.yaml`. Intended rule (README): private YAML is never committed; only `*.example.yaml` are.

Actual state of the working tree (checked with `git ls-files config`): in addition to the five example files, `config/feeds.yaml`, `config/models.yaml` and `config/portals.yaml` are **tracked** (committed before or despite the ignore rule; an ignore pattern does not affect already-tracked files). `config/profile.yaml` and `config/companies.yaml` are absent. Differences from the examples:

- `config/models.yaml` has `score` and `summarize` blocks but no `blurb` block (the example has `blurb`).
- `config/feeds.yaml` enables `foss-united-rss`; the example has both feeds disabled.
- `config/portals.yaml` enables `foss-united`, `null-jobs`, `hasjob-intern`, `huntyourtribe-intern` (the last with `fetch: dynamic`, note text changed); the example has all disabled.

Consequences: local runs and tests that call `load_models()` use the tracked `models.yaml`, and local `collect` runs would fetch the enabled portals and the feed. In the hosted pipeline these are overwritten from secrets (see [github-workflows.md](github-workflows.md#step-8-writing-private-config)). The README's `config/companies.hosted.yaml` does not exist in the repo.

| File | Hosted production source | Local source |
|---|---|---|
| `profile.yaml` | secret `PROFILE_YAML` (required) | copy of `profile.example.yaml` |
| `companies.yaml` | secret `COMPANIES_YAML` (required by pipeline; not used by bot) | copy of `companies.example.yaml` |
| `models.yaml` | secret `MODELS_YAML` (required) | tracked file or copy of example |
| `portals.yaml` | secret `PORTALS_YAML` (required by pipeline) | tracked file or copy |
| `feeds.yaml` | secret `FEEDS_YAML` (required by pipeline) | tracked file or copy |
| `marketplaces.yaml` | secret `MARKETPLACES_YAML` (optional) | copy of `marketplaces.example.yaml`; absent means example fallback |
| `*.example.yaml` | tracked, public stubs | |

## `profile.yaml` (loaded by `load_profile`, `config.py:93`)

Returns the frozen dataclass `Profile` (`config.py:44`). Required keys raise `KeyError` if missing; optional keys have defaults. Values below are from `config/profile.example.yaml`.

| Key | Type | Required / default | Example | Used by (verified by grep) |
|---|---|---|---|---|
| `name` | str | required | `Example` | `blurb.py`, `email_draft.py` (sender name in drafts) |
| `college` | str | required | `Example College` | `blurb.py` |
| `degree` | str | required | `B.E.` | `blurb.py` |
| `batch` | int | required | `2027` | `score.py`, `blurb.py`, `email_draft.py` |
| `cgpa` | float | required | `8.0` | `blurb.py` (the blurb prompt deliberately omits it, per `test_prompt_omits_name_and_cgpa`) |
| `location` | str | required | `Pune` | `score.py`, `rank.py` location preference ("home"), `db/digest_queries.py`, `summarize.py`, `blurb.py` |
| `relocate` | list[str] | required | `[Bengaluru]` | `score.py`, `blurb.py`, `db/digest_queries.py` (second-best location tier) |
| `work_auth` | str | required | `India` | `score.py`, `blurb.py` |
| `role_types` | list[str] | required | `intern, new-grad` | `score.py`, `blurb.py` |
| `skills` | list[str] | required | `Python, FastAPI, ...` | `score.py`, `blurb.py`, `fit.py`, `email_draft.py` |
| `internships` | list[str] | required | `[Example intern]` | `score.py`, `blurb.py` (citations must match one), `bot.py` |
| `india_keywords` | list[str] | required | cities and "PAN India", "Remote India" | `hard_filter.py` (India location test), `expand.py` |
| `title_allow_intern` | regex str | required | `\bintern(?:ship)?s?\b\|apprentice\|trainee\|...` | `hard_filter.py:219` (allow evidence) |
| `title_allow_newgrad` | regex str | required | `new[- ]?grad\|graduate\|junior\|sde\s*1\|software engineer\|...` | `hard_filter.py:223` |
| `title_reject_regex` | regex str | required | `\bstaff\b\|principal\|...\|\bhead of\b` | `hard_filter.py:199`, `score.py:166` |
| `reject_remote_us` | bool | required | `true` | `hard_filter.py:211` |
| `min_yoe_reject` | int | required | `3` | `hard_filter.py` (years-of-experience gate), `score.py` prompt |
| `timezone` | str | required | `Asia/Kolkata` | **loaded but not read anywhere else in `src/`**; IST is hard-coded in `followups.py:6` and elsewhere. Changing it has no effect |
| `digest_max` | int | required | `12` | `digest.py`, `rank.py`, `sheet.py` (max guests per digest) |
| `watchlist` | list[str] | default `[]` | `[]` | `digest.py`, `fit.py` (exact casefold company match bypasses the urgent score threshold) |
| `urgent_score_min` | int | default `85` (empty or null also gives 85) | `85` | `digest.py` urgent slot |
| `urgent_daily_cap` | int | default `3` | `3` | `digest.py` (urgent messages per IST day) |
| `github_url` | str | default `""` | `""` | `email_draft.py` (the GitHub line is omitted when empty) |
| `top_companies` | list[str] | default `[]` | not in the example | `tiers.py` / `fit.py` / `hard_filter.py` (top-company boost; frontend and QA roles allowed only at these) |
| `digest_min_quality` | float | default `50.0` | not in the example | `rank.py:257` quality bar for `select_diverse` |
| `digest_per_company` | int | default `1` | not in example | `rank.py` cap per company |
| `digest_per_portal` | int | default `2` | not in example | `rank.py:262` cap per portal source |
| `digest_company_cooldown_days` | int | default `3` | not in example | `db/digest_queries.py` company cooldown |
| `digest_max_age_days` | int | default `21` | not in example | `rank.py` stale-posting limit |

`_int_field` (line 87) treats a missing key, `null` and `""` as "use the default". `test_profile_has_no_digest_hour` asserts there is no `digest_hour` key; the digest time is controlled by `JOBDIGEST_CRON`, not by the profile. `test_example_profile_has_no_personal_identity` keeps the example anonymous.

Selection mechanics for the `digest_*` fields: see [../modules/ranking/rank-and-selection.md](../modules/ranking/rank-and-selection.md).

## `models.yaml` (`load_models`, `config.py:181`)

Returns `Models` (line 77).

| Key | Meaning | Validation |
|---|---|---|
| `score.model` | OpenRouter model id for batch scoring | must not end with `:free` |
| `score.prompt_version` | tag stored with each score; changing it makes jobs unscored again | read as `str` |
| `score.batch_size` | jobs per LLM call; falls back to a legacy top-level `batch_size`, then 12 | `int` |
| `summarize.model`, `summarize.prompt_version` | digest summary model and tag | same `:free` rule for the model |
| `blurb.model`, `blurb.prompt_version` | optional "Why me" model | if the block exists: model must not be `:free` or `openrouter/auto`; absent block gives `blurb_model=None` and the bot refuses the Why-me button (`test_handle_update_why_me_missing_blurb_config_refuses`) |

Example values: score `openai/gpt-4o-mini` `v5` batch 12; summarize and blurb `anthropic/claude-sonnet-4` `v1`. Tests: `test_config.py` (not free, prompt version non-empty, blurb distinct from score, batch size placement).

## `companies.yaml` (`load_companies`, line 128)

Top-level key `companies:` list. Row keys read by `collect._row_company` (`src/jobdigest/collect.py:55`): `id`, `name`, `ats`, `token`, `enabled` (default False), `notes`. `ats` must be one of the adapters registered in `ats/__init__.py` (`greenhouse`, `lever`, `ashby`, `workable`, `smartrecruiters`, `bamboohr`); other values (the example uses `getro` and `workday`) are rows for ATS types that are not implemented: `get_adapter` raises `ValueError("unimplemented ats")`, so such rows must stay `enabled: false`. Only `enabled: true` rows are fetched. The example file's 11 rows are all disabled stubs (`exampleco`, `huggingface`, `elevation-capital`, `peakxv`, `surge`, `nexusvp`, `flipkart`, `fractal`, `ixigo`, `freshworks-sr`, `zepto`).

Discovered boards: `load_discovered_boards` (line 143) converts `data/boards_india.tsv` rows into the same shape with `id = "<ats>:<token>"`, `enabled: True`, `notes: "discovered by jobdigest.expand"`. `collect` merges them after the curated rows and drops duplicates (e2e-tested); `--skip-discovered` or `JOBDIGEST_DISCOVERED_BOARDS=off` (also `none`, `0`, `false`) disables them. `discovered_boards_path` (line 132) lets the same variable point at another TSV.

## `portals.yaml` (`load_portals`)

Top-level `portals:` list. Keys read in `portals/__init__.py` and `portals/session.py`:

| Key | Meaning |
|---|---|
| `name` | source label stored as `jobs.source`; also the sweep key |
| `url` | page to fetch |
| `enabled` | bool; only true rows run |
| `parser` | one of `foss_united`, `null_community`, `hasjob`, `generic_cards` (the `PARSERS` dict, `portals/__init__.py:26`). The value `kill` is not in `PARSERS`, so a `kill` row, if ever enabled, returns a parse error `unknown parser 'kill'`; by convention these rows are the "kill list" of sites that must not be scraped |
| `fetch` | `http` (default, `FETCH_HTTP`), `dynamic` (Playwright via Scrapling) or `stealth`; anything else is a parse error |
| `solve_cloudflare` | optional bool, stealth mode only: passed through to the fetcher |
| `notes` | free text |

The example has 24 rows; 11 are real boards (foss-united, null-jobs, hasjob, hasjob-intern, huntyourtribe-intern with `fetch: dynamic`, prepnplaced-apprentice, puneops, aikyam-jobs, cybertimes-jobs, remotist, agentic-engineering-india) and 13 are `parser: kill` entries (otta, huggingface-jobs, llmhire, discord, linkedin, internshala, cutshort, instahyre, hirist, wellfound, weekday, perik, ai-jobs-net).

## `feeds.yaml` (`load_feeds`)

Top-level `feeds:` list with `name`, `url`, `enabled`, `notes`. Parsed by `feeds.py` with `feedparser` over `httpx`. Example: `python-jobs` and `foss-united-rss`. A feed whose URL equals a portal job's URL dedupes into one row (tested in `test_collect_portals.py`).

## `marketplaces.yaml` (`load_marketplaces`)

Top-level `marketplaces:` list; each row is one JobSpy search. Keys passed to JobSpy by `jobspy_bridge._scrape_kwargs` (line 109): `site` (one of `linkedin, indeed, zip_recruiter, glassdoor, google, bayt, naukri, bdjobs`), `search_term`, `location`, `results_wanted` (default 30), `google_search_term`, `country_indeed`, `hours_old`, `job_type`, `is_remote`, `linkedin_fetch_description` (defaults to true for `naukri`); plus `name` (becomes `jobs.source`), `enabled`, `notes`. Proxies come from env `JOBDIGEST_MARKETPLACE_PROXIES` (comma separated). Requires the `marketplaces` extra. All 8 example rows are disabled.

## Data files (`data/`)

| File | Rows (excluding header) | Columns | Written by | Read by |
|---|---|---|---|---|
| `ats_board_tokens_all.tsv` | 64,655 | `company, ats, token, sources` | external research dataset (origin not documented in repo) | nothing in `src/` or `scripts/` references it |
| `ats_board_tokens_gla.tsv` | 19,042 | same; only greenhouse (9,750), lever (4,822), ashby (4,470) | subset of the above | `expand.py` default input `--tsv data/ats_board_tokens_gla.tsv` |
| `ats_board_tokens_gla_2000.tsv` | 2,000 | same; ashby 1,195, greenhouse 805 | sample subset | no code reference |
| `boards_india.tsv` | 222 | `company, ats, token, candidates, sample` (5 columns; greenhouse 140, lever 44, ashby 38) | `python -m jobdigest.expand` (`DEFAULT_OUT = "data/boards_india.tsv"`) | `config.load_discovered_boards` (reads columns 0-2 only), used by `collect` |

The `_all` file additionally contains workday (13,337), bamboohr (11,324), paylocity (10,252), icims (10,106), smartrecruiters (271) and other ATS rows; only greenhouse, lever and ashby are probe-able by `expand` (`_SUPPORTED`). `sources` is a comma list of upstream lists (e.g. `feashliaa,job-tracker-v2,lastround`). `boards_india.tsv` columns: `candidates` is the number of promising roles found, `sample` the first matching title.

Git status: the three `ats_board_tokens_*` files are tracked; `data/boards_india.tsv` is **untracked** in the working tree (`??` in `git status`), so a hosted run, which only sees committed files, would find no discovered boards until it is committed. `data/*.db*` is gitignored.

## Environment variables (`.env.example`)

`load_dotenv()` (`src/jobdigest/envfile.py:38`) reads `<repo>/.env` and sets only keys not already in the environment, so real environment (including Actions) wins. It supports `export KEY=val`, quotes, and ` #` inline comments. It is called by `cli.main`, `collect.run`, `digest.run`, `score.run`, `contacts.run.run`, `bot.main`, `sheet`.

| Variable | In `.env.example` | Meaning | Read in |
|---|---|---|---|
| `OPENROUTER_API_KEY` | yes (empty) | LLM key | `openrouter.py`, `score.py`, `digest.py`, `bot.py` |
| `DATABASE_PATH` | `data/jobs.db` | SQLite file when `DATABASE_URL` empty; relative paths resolve against repo root | `db/connection.py:169` |
| `DATABASE_URL` | yes (empty) | Postgres URL; wins whenever non-empty and no explicit `--db` | `db/connection.py:161` |
| `JOBDIGEST_CRON` | `30 3 * * *` | digest slot, UTC | `cron.py` |
| `JOBDIGEST_CRON_URGENT` | empty | urgent slot; empty means `30 8 * * 1-5`, `off` disables | `cron.py` |
| `JOBDIGEST_LOG_LEVEL` | `INFO` | logging level | `logutil.py:11` |
| `JOBDIGEST_LOG_FILE` | empty | optional log file | `logutil.py` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | yes | Telegram credentials | `telegram.py` |
| `GOOGLE_SHEET_ID` | yes | spreadsheet id | `sheet.py` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | yes | path to the service-account JSON file (relative paths resolve against repo root); in hosted mode the pipeline writes the secret text to `google-sa.json` and passes that path | `sheet.py:84` |
| `JOBDIGEST_REQUIRE_SHEET` | yes (empty) | `1/true/yes` turns missing sheet credentials into exit 1 | `sheet.py:52` |
| `JOBDIGEST_MARKETPLACE_PROXIES` | yes | comma list of proxies | `marketplaces/__init__.py:12` |
| `JOBDIGEST_GITHUB_TOKEN` | yes | PAT for contacts lookups | `contacts/github.py:21`, `contacts/run.py:91` |

Variables read by code but absent from `.env.example`: `JOBDIGEST_REQUIRE_TELEGRAM` (`telegram.py:65`; set by workflows), `JOBDIGEST_CONFIG_DIR`, `JOBDIGEST_DISCOVERED_BOARDS`, `OPENROUTER_REFERER` (`openrouter.py:81`, optional `HTTP-Referer` header), `GITHUB_EVENT_NAME` and `GITHUB_OUTPUT` (provided by Actions to the gate), `JOBDIGEST_SLUG_DIR` (scripts only). The scaffold test requires the keys listed in [../testing/strategy.md](../testing/strategy.md#teststest_scaffold_constraintspy-6-tests) to stay in the example file.

## Email templates (`src/jobdigest/templates/email/`)

Packaged as package data (`pyproject.toml`: `jobdigest = ["templates/email/*"]`) and used by `email_draft.py` (`_TEMPLATES` at line 22).

- `cold_outreach.j2`: Jinja2 template beginning `Subject: {{ title }} — {{ your_name }}`; variables `title`, `your_name`, `contact_name` (defaults "there"), `company`, `product_observation`, `skills` (joined), `batch`, optional `github_url` block.
- `recruiter_outreach.j2`: subject `{{ title }} application — {{ your_name }} ({{ batch }} grad)`, same variables, used when the contact is a recruiter (`test_draft_uses_recruiter_template_and_omits_empty_github`).
- `fallbacks.yaml`: key `observations`, a list of three generic sentences chosen deterministically when no job-specific observation exists (`test_fallback_selection_deterministic_and_uses_all`).

See [../modules/delivery/email-drafts.md](../modules/delivery/email-drafts.md).

## README drift relevant to this page

- `config/companies.hosted.yaml` is mentioned in README but not present.
- README says private YAML is "ignored by git", yet three are tracked (see above).
- `timezone` appears in the profile example and loader but has no effect.
