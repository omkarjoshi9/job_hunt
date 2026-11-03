# Test strategy

- Source: `tests/` (61 `test_*.py` files plus `tests/conftest.py`, about 15,100 lines) and `tests/fixtures/` (45 data files)
- Role: verification layer for the whole package; also enforces architectural and deployment constraints as tests
- Configured by: `[tool.pytest.ini_options]` in `pyproject.toml` (`testpaths = ["tests"]`, `pythonpath = ["src"]`)
- Companion pages: [test-map.md](test-map.md) (file by file), [e2e-and-fixtures.md](e2e-and-fixtures.md) (the pipeline test and the fixture tree), [../infra/github-workflows.md](../infra/github-workflows.md) (how CI runs them)

Current result in the working tree (checked 2026-11-03, Python 3.12 venv): `732 collected, 731 passed, 1 skipped` in about 25-30 seconds. The skip is `tests/test_marketplaces_bridge.py:151` (`could not import 'pandas'`; pandas arrives with the optional `python-jobspy` extra, which CI does not install).

## Philosophy: offline by design

The whole suite runs with no network, no real secrets and no external service. This is a design rule, not an accident, and it is achieved in three layers:

1. **Dependency injection at every boundary.** Production functions that touch the outside world accept the thing they call as an optional keyword argument. Tests pass a fake; production passes nothing and the function builds the real one. There is no mocking of internals in the common case.
2. **A global safety net.** An autouse fixture in `tests/conftest.py` clears Telegram credentials and replaces the real HTTP POST with a function that raises. A test that forgets to inject a fake fails loudly instead of messaging a real chat.
3. **Real code on a real (temporary) database.** Tests do not mock the database layer. They call `connect(tmp_path / "jobs.db")`, which creates a real SQLite file in pytest's temporary directory with the production schema, then run the real SQL. Only Postgres is never exercised live (see [Postgres](#postgres-is-tested-by-string-and-seam-only)).

Live HTTP is explicitly out of scope: the README says that live probing is for maintainers via `python -m jobdigest.probe`, not for CI.

## Running the tests

```bash
python3 -m venv .venv && source .venv/bin/activate     # .venv/ and venv/ are gitignored
pip install -e ".[dev]"                                # dev extra = pytest>=8.0
pytest                                                 # whole suite
pytest tests/test_hard_filter.py -q                    # one file
pytest tests/test_fit.py -k quality                    # by keyword
pytest -rs                                             # show why tests were skipped
```

Notes:

- `pythonpath = ["src"]` means tests import `jobdigest` straight from `src/` even without the editable install. The editable install is still needed for the heavy runtime dependencies (`scrapling`, `playwright`, `psycopg`, ...), which the package imports.
- CI does exactly `pip install -e ".[dev]"` then `pytest` (`.github/workflows/ci.yml`); see [github-workflows.md](../infra/github-workflows.md#ciyml).
- Tests import helpers from each other (`tests/test_pipeline_e2e.py` does `from test_sheet import FakeSheet`; `tests/test_bot_buttons.py` does `from test_bot import NOW, _mail_setup, _mail_update, _OkPost`). That works because pytest's default import mode puts `tests/` on `sys.path`; there is no `tests/__init__.py`. Renaming `test_sheet.py` or `test_bot.py` would break those importers.
- No pytest plugins, markers or coverage tool are configured.

## The global safety net: `_no_live_telegram`

**Location** `tests/conftest.py:7` (`@pytest.fixture(autouse=True)`). Every test in the suite gets it automatically.

Step by step:

1. `monkeypatch.setenv("JOBDIGEST_DISCOVERED_BOARDS", "off")`. `collect` normally also fetches every board listed in `data/boards_india.tsv` (see `src/jobdigest/config.py:132` `discovered_boards_path`). Without this line, any test that runs `collect.run` would silently fetch roughly 220 extra boards through its fake HTTP client. Tests that want discovered boards point the variable at a temp TSV themselves (`test_collect_includes_discovered_boards_without_duplicating_curated` in `tests/test_pipeline_e2e.py`).
2. `delenv("TELEGRAM_BOT_TOKEN")`, `delenv("TELEGRAM_CHAT_ID")`, `delenv("JOBDIGEST_REQUIRE_TELEGRAM")`. A developer's real `.env` is loaded by `load_dotenv()` inside `collect.run`, `digest.run` and others, but `load_dotenv` never overrides keys already present in `os.environ` (`src/jobdigest/envfile.py:38`); removing them before each test and re-adding only what the test wants keeps the environment deterministic. (A `.env` file in the repo root could still re-supply a missing key; the fixture does not stub `load_dotenv`.)
3. `monkeypatch.setattr("jobdigest.telegram._httpx_post", _blocked)`. `_httpx_post` (`src/jobdigest/telegram.py:70`) is the single function that really calls `httpx.post`. The replacement raises `AssertionError("test attempted a live Telegram POST to ... inject http_post")`.

Tests that exercise sending either inject an `http_post` callable (the normal pattern) or set the env vars and patch `_httpx_post` themselves. `test_live_telegram_url_only_in_telegram_module` (`tests/test_telegram.py:114`) additionally asserts that the string `api.telegram.org` appears in no source file except `telegram.py`, so there is exactly one place to guard.

## The injection seams

These are the keyword arguments tests use to replace the outside world. All are optional in production code.

| Seam | Where (source) | What the fake is | Typical tests |
|---|---|---|---|
| `client` (an `httpx.Client`) | `collect.run`, ATS adapters (`GreenhouseAdapter(client, sleep=...)`), `contacts.run.run`, `expand`, `probe` | `httpx.Client(transport=httpx.MockTransport(handler))`; the handler returns canned `httpx.Response` objects and can assert on the request URL and params | every `test_ats_*.py`, `test_collect*.py`, `test_probe.py`, `test_expand.py`, `test_contacts_*.py`, `test_pipeline_e2e.py` |
| `sleep` | `collect.run`, adapters, `feeds.run_one`, portal session | `lambda _s: None`, or a recorder list to assert that a back-off or politeness delay was requested | `test_ats_greenhouse.py` (`test_429_then_200_empty_calls_sleep`), `test_portal_session.py` |
| `fetch_fn` | `collect.run(fetch_fn=...)` (default `_default_fetch`) | `def fetch_fn(company, client, sleep) -> FetchResult` returning a scripted `FetchStatus` | `test_collect.py`, `test_tombstone.py`, `test_health.py`, `test_pipeline_e2e.py` |
| `portal_run_fn`, `feed_run_fn`, `marketplace_run_fn` | `collect.run` | lambdas returning `PortalResult` | `test_collect_portals.py`, `test_collect_marketplaces.py` |
| `get_html_fn`, `get_html` | `portals.run_one`, `portals.session` | returns `(status, html)` from a fixture file instead of a browser | `test_portals_parsers.py`, `test_portal_session.py` |
| `http_get` | `feeds.run_one` | returns a canned feed body | `test_feeds.py` |
| `scrape_fn` | `marketplaces.run_one` | replaces the JobSpy `scrape_jobs` call (no pandas needed) | `test_marketplaces_bridge.py` |
| `chat_fn` / `score_chat_fn` / `summarize_chat_fn` | `score.run`, `digest.run`, `summarize`, `blurb`, `bot.handle_update` | a function with the signature of `openrouter.chat` returning a JSON string or raising `OpenRouterError` | `test_score.py`, `test_summarize.py`, `test_blurb.py`, `test_digest*.py`, `test_pipeline_e2e.py` (`fake_llm`) |
| `http_post` / `telegram_http_post` | `telegram.deliver`/`send_message`, `bot.handle_update`/`poll_once`/`drain`/`run_for`, `collect.run`, `digest.run` | a callable `(url, json=None, timeout=...) -> response-like` that records every call | `test_telegram.py`, `test_bot*.py`, `test_urgent.py`, `test_digest_telegram.py` |
| `worksheet` | `sheet.run`, `sheet.sync` | `FakeSheet` (`tests/test_sheet.py:35`), an in-memory worksheet implementing `get_all_values`, `append_row`, `update`, `format`, `freeze`, `add_validation` | `test_sheet.py`, `test_pipeline_e2e.py` |
| `now` | ranking, digest, urgent, followups, bot (`handle_update(..., now=NOW)`) | a fixed `datetime` so that recency boosts, 36-hour lookbacks and IST day boundaries are deterministic | `test_rank.py`, `test_urgent.py`, `test_priors.py`, `test_followups.py` |
| `clock` and `sleep` for the bot loop | `bot.run_for` | a fake monotonic clock advanced by the test | `test_bot_buttons.py::test_run_for_stops_at_its_deadline` |
| `db_path` / `conn` | every stage's `run`, `bot.handle_update` | `tmp_path / "jobs.db"` or an already-open connection | nearly all DB-touching tests |
| `monkeypatch.setattr("jobdigest.collect.load_companies", ...)` | module-level config loaders | lambdas returning dicts, used when a test does not want to write YAML | `test_collect*.py`, `test_tombstone.py`, `test_health.py` |

The `FetchResult`/`FetchStatus` types behind `fetch_fn` are documented in [../modules/sources/ats-base-and-http-client.md](../modules/sources/ats-base-and-http-client.md).

Counts of how widespread each seam is (grep over `tests/*.py`): `tmp_path` appears in 31 files, `httpx.MockTransport` in 14, `monkeypatch.setattr` in 13 (130 uses), `monkeypatch.setenv` in 17 (127 uses), `http_post` in 8, `chat_fn` in 10, `fetch_fn` in 6.

### Environment variables tests set

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (about 45 uses each, to turn sending on), `JOBDIGEST_CONFIG_DIR` (14, to point config loading at a temp directory), `JOBDIGEST_DISCOVERED_BOARDS` (3), `JOBDIGEST_CRON` / `GITHUB_EVENT_NAME` / `GITHUB_OUTPUT` (cron gate tests), `DATABASE_URL` (Postgres routing tests), `JOBDIGEST_REQUIRE_TELEGRAM`, `JOBDIGEST_REQUIRE_SHEET`, `JOBDIGEST_MARKETPLACE_PROXIES`, `JOBDIGEST_GITHUB_TOKEN`, `GITHUB_TOKEN`.

## Where test configuration comes from

Most tests call `load_profile()` and `load_models()` with no `JOBDIGEST_CONFIG_DIR`. `src/jobdigest/config.py:23` (`_yaml_path`) then looks in the repo's `config/` directory and **falls back to `<name>.example.yaml`** (with a logged warning) when the private file is missing. Consequences:

- `config/profile.yaml` is gitignored and absent in a fresh clone and in CI, so tests run against `config/profile.example.yaml` (Pune, batch 2027, `digest_max: 12`, ...). 22 test files call `load_profile()` directly.
- If a developer has a real `config/profile.yaml` locally, those tests silently read it instead. Assertions that depend on example values (home city Pune, relocation to Bengaluru, thresholds) can then fail locally but pass in CI. This is a latent environment-sensitivity of the suite, not a documented guard.
- `config/models.yaml`, `config/portals.yaml` and `config/feeds.yaml` are tracked in git despite the `config/*.yaml` ignore rule (they were committed before or despite it), so tests calling `load_models()` read the tracked `config/models.yaml` (no `blurb:` block), not the example. See [../infra/config-files.md](../infra/config-files.md#tracked-versus-private).
- Tests that need specific config write their own YAML into `tmp_path` and set `JOBDIGEST_CONFIG_DIR` (14 uses, the e2e `env` fixture among them).

## Test categories

| Category | Files | Style |
|---|---|---|
| Source adapters | `test_ats_*.py` (9), `test_http_client.py`, `test_portals_parsers.py`, `test_portal_session.py`, `test_feeds.py`, `test_marketplaces_bridge.py`, `test_discover.py`, `test_probe.py`, `test_expand.py` | Feed canned payloads (fixtures or inline dicts) through `MockTransport`; assert normalised `NormalizedJob` fields and `FetchStatus` classification |
| Text normalisation, filtering | `test_normalize_*.py`, `test_hard_filter.py` (87 tests), `test_legitimacy.py`, `test_content_hash.py`, `test_dual_hash.py` | Pure-function table tests; the hard filter file is regression-heavy with one test per production false positive or negative |
| Storage | `test_db_*.py`, `test_tombstone.py`, `test_priors.py` (DB-backed parts) | Real SQLite in `tmp_path`; assert schema, migrations, upsert identity, sweep counters |
| Ranking and selection | `test_rank.py`, `test_fit.py`, `test_priors.py`, `test_score.py`, `test_summarize.py`, `test_blurb.py` | Deterministic arithmetic plus fake LLM responses |
| Delivery | `test_digest*.py`, `test_urgent.py`, `test_telegram.py`, `test_bot.py`, `test_bot_buttons.py`, `test_sheet.py`, `test_followups.py`, `test_health.py`, `test_email_draft.py` | Recorded fake Telegram and `FakeSheet` |
| Contacts | `test_contacts_*.py`, `test_db_contacts.py` | MockTransport + fixture JSON |
| Orchestration | `test_collect*.py`, `test_cron.py`, `test_cli.py`, `test_pipeline_e2e.py` | Whole stage `run()` with every seam faked |
| Constraint (architecture and deployment) | `test_scaffold_constraints.py`, `test_no_stealthy.py`, `test_workflows.py`, `test_collect_no_llm.py`, plus single tests inside other files | Read source or YAML as text and assert that forbidden things are absent |

## Constraint tests: what each forbids

These tests read repository files as text (or parse YAML) and fail if a rule is broken. They are the project's executable architecture rules.

### `tests/test_scaffold_constraints.py` (6 tests)

| Test | Rule it enforces |
|---|---|
| `test_pyproject_has_no_forbidden_deps` | The string `openai` must not appear anywhere in `pyproject.toml` (the project talks to OpenRouter over plain `httpx`, never the OpenAI SDK). Also requires `scrapling[fetchers]`, `playwright==1.62.0`, `patchright==1.62.1` to be present, so bumping one of these requires editing the test. |
| `test_pyproject_has_httpx_scrapling_feedparser_gspread` | `httpx`, `scrapling`, `feedparser`, `gspread`, `psycopg`, `jinja2` all appear in `pyproject.toml`. |
| `test_python_jobspy_is_optional_extra_only` | `python-jobspy` is not in the core `dependencies` and is in the `marketplaces` extra. Keeps CI fast and pandas-free. |
| `test_no_requirements_txt` | There is no `requirements.txt`; `pyproject.toml` is the only dependency declaration. |
| `test_env_example_has_i7_keys` | `.env.example` lists `DATABASE_URL=`, `JOBDIGEST_CRON=`, `JOBDIGEST_CRON_URGENT=`, `TELEGRAM_BOT_TOKEN=`, `TELEGRAM_CHAT_ID=`, `JOBDIGEST_REQUIRE_SHEET=`, `JOBDIGEST_MARKETPLACE_PROXIES=`, `JOBDIGEST_GITHUB_TOKEN=`; and must not contain `JOBDIGEST_ENABLE=`. ("i7" refers to an internal iteration label; the meaning is not documented in the repo.) |
| `test_readme_drops_hermes_documents_hosted` | `README.md` must not contain `hermes`, `openwa`, `30 4 * * *`, `collect-browser`, `http-only fetching`, `no playwright, chromium`; and must contain (among others) `30 3 * * *`, `botfather`, `neon`, `--ats-only`, `--skip-marketplaces`, `[marketplaces]`, `--portal-fetch`, `playwright install chromium`, `feedparser`, `wheels`, `jobdigest.bot`, `jobdigest_cron_urgent`, `/followups`, `jobdigest_github_token`, `contacts`. This makes the README a tested artifact: deleting those words fails CI. |

### `tests/test_no_stealthy.py` (2 tests)

- `test_fetcher_class_names_only_under_portals` walks every `*.py` under `src/jobdigest`. Outside the `portals/` package none of `StealthyFetcher`, `DynamicFetcher`, `FetcherSession`, `DynamicSession`, `StealthySession` may appear. Inside `portals/`, only `StealthyFetcher` and `DynamicFetcher` are forbidden (the session classes are allowed there, because `portals/session.py` is the one place that uses them).
- `test_pipeline_modules_have_no_scrapling_sessions` checks `collect.py`, `score.py`, `digest.py`, `cli.py`, `feeds.py`: none contain any of those five names, and none contain the word `scrapling` (case-insensitive).

Why: browser automation is confined to the portal lane so that the ATS, RSS, scoring and delivery code stays importable and testable without Chromium.

### `tests/test_collect_no_llm.py` (2 tests)

- `collect.py` (lowercased) must not contain `openrouter`, `openai`, `scrapling`, `gspread`, `jobdigest_enable`, `jobspy`, `pandas`, `jobdigest.contacts`. Collection is an LLM-free, Sheets-free stage.
- `score.py`, `digest.py`, `bot.py`, `blurb.py` must not contain `gspread`.

### `tests/test_workflows.py` (8 tests)

| Test | Rule |
|---|---|
| `test_ci_stays_pytest_only` | `ci.yml` contains `pytest` and none of: `cron`, `telegram` (any case), `DATABASE_URL`, `PROFILE_YAML`, `MODELS_YAML`, `PORTALS_YAML`, `FEEDS_YAML`, `MARKETPLACES_YAML`, `[marketplaces]`, `GOOGLE_SHEET`, `sheet-sync`, `playwright install`, `chromium`. CI must never need a secret, the hosted DB, Chromium or JobSpy. |
| `test_old_workflows_removed` | `collect.yml`, `collect-browser.yml`, `digest.yml` must not exist in `.github/workflows/`. |
| `test_pipeline_workflow` | Pins ~50 substrings of `pipeline.yml`: cron `*/15 * * * *`, `workflow_dispatch`, concurrency group `jobdigest-pipeline`, `cancel-in-progress: false`, `timeout-minutes: 60`, `python3 -m jobdigest.cron`, the four stage commands, `Contacts enrich`, Chromium install steps and `~/.cache/ms-playwright`, Python 3.12, every required secret reference, `JOBDIGEST_REQUIRE_TELEGRAM`, `JOBDIGEST_REQUIRE_SHEET`, `pip install -e ".[marketplaces]"`. Forbids `--portal-fetch`, `--skip-ats`, `--skip-feeds`, `--skip-marketplaces`, `--ats-only` (hosted collect runs every lane), `JOBDIGEST_ENABLE`, literal cron expressions `30 3 * * *`/`30 4 * * *`/`0 4 * * *` (the schedule lives in the `JOBDIGEST_CRON` secret, not in YAML), `git commit`, `jobs.db`, and `sk-or-` (an OpenRouter key prefix, to catch pasted keys). |
| `test_start_sh_uses_http_portals` | `start.sh` contains `--portal-fetch http`, `digest`, `contacts`, `sheet-sync`; not `--ats-only`, not `jobdigest.bot`. |
| `test_start_sh_does_not_start_bot` | `start.sh` has no `jobdigest.bot`. |
| `test_pipeline_does_not_start_bot` | `pipeline.yml` has no `jobdigest.bot` (the bot has its own workflow). |
| `test_pipeline_and_start_have_no_discover` | `jobdigest.discover` must not appear in `pipeline.yml` or `start.sh` (a similar check on `ci.yml` is in `test_discover.py`). |
| `test_pipeline_urgent_branch` | `pipeline.yml` contains `secrets.JOBDIGEST_CRON_URGENT`, `--urgent-only`, `needs.gate.outputs.slot`; must not contain `30 8` or `30 3 * * *` or `jobdigest.bot`. |

`tests/test_pipeline_e2e.py` and `tests/test_bot_buttons.py` add structural (parsed-YAML) workflow tests; see [e2e-and-fixtures.md](e2e-and-fixtures.md#workflow-wiring-tests).

### Single constraint tests inside other files

| Test | Rule |
|---|---|
| `test_telegram.py::test_live_telegram_url_only_in_telegram_module` | `api.telegram.org` appears only in `telegram.py`. |
| `test_cron.py::test_cron_module_is_stdlib_only` | `cron.py` contains none of `gspread`, `httpx`, `openrouter`, `openai`. The gate job runs `python3 -m jobdigest.cron` **without** `pip install`, so the module must import on a bare runner. |
| `test_feeds.py::test_feeds_module_stays_httpx_feedparser` | `feeds.py` mentions `feedparser` and `httpx`, not `scrapling`, `XMLFeedSpider`, `Fetcher.get`. |
| `test_contacts_pipeline.py::test_contacts_package_has_no_banned_integrations` | The `contacts` package source contains no `playwright`, `linkedin.com`, `smtplib`, `openrouter`, `hunter.io`, and never reads `os.environ.get("GITHUB_TOKEN")` (the contacts lane only reads `JOBDIGEST_GITHUB_TOKEN`, so the Actions-provided token is never used by accident). |
| `test_digest.py::test_digest_source_does_not_import_blurb` | `digest.py` has no `jobdigest.blurb`, `draft_blurb`, `jobdigest.contacts`; `score.py` has no `draft_blurb`. LLM-written blurbs and contact lookup are on-tap bot features, not part of the batch digest. |
| `test_sheet.py::test_collect_score_digest_source_has_no_gspread` | `collect.py`, `score.py`, `digest.py` do not mention `gspread`. |
| `test_discover.py::test_workflows_pipeline_has_no_discover` | `jobdigest.discover` in none of `pipeline.yml`, `start.sh`, `ci.yml`. |
| `test_discover.py::test_discover_does_not_write_companies_path` | The `discover` tool never writes `companies.yaml`. |
| `test_config.py` | `models.yaml` rejects OpenRouter `:free` ids; the example blurb model is paid and distinct from the score model; the example profile contains no personal identity. |
| `test_pipeline_e2e.py::test_pipeline_workflow_*` and `test_pipeline_keeps_state_between_runs_even_without_a_database_secret` | Parsed-YAML checks of stage order, gating and the state cache. |
| `test_bot_buttons.py::test_bot_workflow_*` | `bot.yml` has a schedule and `workflow_dispatch`; `cancel-in-progress` is `False`; the `--max-seconds` value is less than `timeout-minutes * 60`; a guard step that exits 1 on empty `DATABASE_URL` runs before the poller step. |

## Postgres is tested by string and seam only

`tests/test_db_postgres.py` (4 tests) and `tests/test_db_internals.py` never connect to a Postgres server. They check: that `connect(path)` with an explicit path ignores `DATABASE_URL` (`uses_postgres(path)` is true only when `path is None`), that `uses_postgres(None)` follows the env var, that `SCHEMA_PG` text keeps the identity rules (`apply_url_norm TEXT NOT NULL UNIQUE`, `GENERATED BY DEFAULT AS IDENTITY`, the `CHECK` constraints, the `jobs_source_ext` index), and that the SQL translator `_pg_sql` rewrites `?` placeholders (but not `?` inside string literals) and adds `RETURNING id` only for tables with an `id` column. A real Postgres round trip is not covered by any automated test. See [../modules/storage/connection-and-postgres-shim.md](../modules/storage/connection-and-postgres-shim.md).

## Writing a new test: checklist

1. Use `tmp_path` for any database: `conn = connect(tmp_path / "jobs.db")`. Pass `db_path` or `conn` explicitly so `DATABASE_URL` cannot redirect to Postgres.
2. Replace every outbound call through its seam; never rely on the autouse blocker as the primary mechanism (it only catches mistakes).
3. If the code under test needs Telegram, set `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` with `monkeypatch.setenv` and inject `http_post`/`telegram_http_post`.
4. For deterministic time, pass `now=`. Do not freeze time globally; there is no freezegun.
5. For config, either rely on the example-file fallback or write temp YAML and set `JOBDIGEST_CONFIG_DIR`.
6. For a new source adapter, add a JSON fixture under `tests/fixtures/ats/<name>/` (see [e2e-and-fixtures.md](e2e-and-fixtures.md#the-fixture-tree)) and cover the `FetchStatus` outcomes every adapter test already covers: OK with jobs, empty board, 404 dead token, invalid body, HTML challenge page, pagination limits if any.
7. If your change adds a new forbidden-import rule or a new workflow step, add it to the matching constraint test rather than documenting it only in prose.
