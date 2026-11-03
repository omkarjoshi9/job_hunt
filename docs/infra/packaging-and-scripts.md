# Packaging, entry points and scripts

- Source: `pyproject.toml` (43 lines), `start.sh`, `scripts/` (3 files), `.gitignore`, `src/jobdigest/__main__.py`, `src/jobdigest/cli.py`
- Related: [github-workflows.md](github-workflows.md), [config-files.md](config-files.md), [../modules/foundation/cli-and-entrypoints.md](../modules/foundation/cli-and-entrypoints.md), [../modules/sources/research-scripts.md](../modules/sources/research-scripts.md)

## `pyproject.toml`

- Build system: `setuptools>=68` and `wheel` (`setuptools.build_meta`).
- Project: name `jobdigest`, version `0.1.0`, `requires-python = ">=3.11"` (CI and the hosted workflows use 3.12), license MIT, readme `README.md`, author "Omkar".
- Package discovery: `[tool.setuptools.packages.find] where = ["src"]` (src layout). Package data: `jobdigest = ["templates/email/*"]`, so the Jinja templates and `fallbacks.yaml` ship in wheels and editable installs.
- `src/jobdigest.egg-info/` exists in the working tree (an editable-install artefact; gitignored via `*.egg-info/`).

### Core dependencies

| Dependency | Pin | Why it is needed |
|---|---|---|
| `pyyaml` | `>=6.0` | config loading (`config.py`) |
| `httpx` | `>=0.27` | all plain HTTP: ATS adapters, Telegram, OpenRouter, contacts, probe |
| `scrapling[fetchers]` | `==0.4.15` | portal fetching sessions (`portals/session.py`), the only place Scrapling may be imported |
| `playwright` | `==1.62.0` | browser backend for `fetch: dynamic` / `stealth`; the version is duplicated in the Chromium cache key of `pipeline.yml` |
| `patchright` | `==1.62.1` | patched Playwright fork used by Scrapling's stealth mode |
| `curl_cffi`, `browserforge` | unpinned | transitive needs of Scrapling's fetchers, declared explicitly |
| `protego` | unpinned | robots.txt parsing (`portals/robots.py`) |
| `feedparser` | unpinned | RSS/Atom (`feeds.py`) |
| `gspread` | `>=6.1` | Google Sheets (`sheet.py`) |
| `jinja2` | `>=3.1` | email draft templates |
| `psycopg[binary]` | `>=3.1` | Postgres (Neon) driver, imported lazily in `connect_postgres` |

The README explains that Playwright wheels are always required (so `FetcherSession` can import) while Chromium binaries are needed only on the hosted pipeline or when a dynamic/stealth portal is enabled.

### Optional extras

| Extra | Contents | Installed by |
|---|---|---|
| `dev` | `pytest>=8.0` | CI, local development |
| `marketplaces` | `python-jobspy>=1.1.82` (brings pandas) | `pipeline.yml`, local marketplace lane |

### Entry points

- Console script: `jobdigest = "jobdigest.cli:main"` (`src/jobdigest/cli.py:176`). With no subcommand it prints help and returns 0.
- `python -m jobdigest` runs `src/jobdigest/__main__.py`, which calls `cli.main()`.
- Other runnable modules (not console scripts): `python -m jobdigest.cron` (workflow gate), `python -m jobdigest.bot [--once | --max-seconds N] [--db PATH]`, `python -m jobdigest.probe`, `python -m jobdigest.discover`, `python -m jobdigest.expand [--tsv --out --workers --limit]`.

### CLI subcommands (`cli.build_parser`)

| Command | Purpose | Notable flags |
|---|---|---|
| `collect` | fetch ATS, discovered boards, portals, feeds, marketplaces into the DB | `--ats-only`, `--skip-ats`, `--skip-discovered`, `--skip-portals`, `--skip-feeds`, `--skip-marketplaces`, `--portal-fetch {http,dynamic,stealth}` (repeatable), `--db` |
| `score` | score pass jobs through OpenRouter | `--db` |
| `digest` | score then print and send the Telegram digest | `--urgent-only`, `--db` |
| `sheet-append` | append applied jobs to Google Sheets | `--url --company --title --status --notes --source --db` |
| `sheet-sync` | append new off-campus digest guests to the sheet, skipping existing URLs | `--db` |
| `contacts` | enrich hiring-manager contacts for the last digest's guests | `--limit`, `--job-id` (repeatable), `--db` |

Every command's `--db` help reads "SQLite path (default DATABASE_PATH or data/jobs.db)". Passing `--db` forces SQLite even when `DATABASE_URL` is set (`uses_postgres` requires `path is None`); `start.sh` relies on that.

## `start.sh`

Local runner, 19 lines. `set -euo pipefail`, `cd` to the script's directory, `export PYTHONPATH=src`, `JOBDIGEST_LOG_LEVEL` defaulting to `DEBUG`. It picks `python` or `python3` (exits 1 if neither). It then runs four commands sequentially against the local SQLite file:

```bash
"$PY" -m jobdigest collect --portal-fetch http --db data/jobs.db
"$PY" -m jobdigest digest --db data/jobs.db
"$PY" -m jobdigest contacts --db data/jobs.db
"$PY" -m jobdigest sheet-sync --db data/jobs.db
```

Why `--portal-fetch http`: it limits portals to those that need no browser, so a laptop without Chromium can run the full pipeline (`test_portal_fetch_http_skips_dynamic_rows`). It differs from the hosted pipeline, which runs all fetch modes. `start.sh` does not start the bot or run `discover` (both asserted by `tests/test_workflows.py`), and `set -e` stops at the first failing stage. It uses `--db`, so it ignores a `DATABASE_URL` in the environment. `.env` is loaded by the Python code itself, not by the shell script.

## `scripts/` (maintainer research tools)

Not part of the installed package and never run by CI.

| File | Purpose |
|---|---|
| `scripts/_probe_common.py` | shared helpers: `SLUG_DIR_ENV = "JOBDIGEST_SLUG_DIR"`, expected slug-list filenames (`lever_slugs.txt`, `ashby_slugs.txt`, `hire_signal.txt`, `workable_candidates.txt`), `add_slug_dir_arg`, `resolve_slug_dir` (exits with a message when no directory is given), `confidence(jobs, notable)` returning high/medium/low |
| `scripts/probe_ats_tokens.py` | probes public Lever/Ashby/Workable board APIs for curated slug lists, filters to notable companies, skips tokens already in `companies.yaml`, writes YAML for boards that return valid JSON to `config/research_ats_boards.yaml` |
| `scripts/probe_ats_tokens_wave2.py` | second-wave Lever probes using a hard-coded `WAVE2` dict, same output file |

The slug directory has no default; the scripts require `--slug-dir` or `JOBDIGEST_SLUG_DIR`. Output `config/research_ats_boards.yaml` is covered by the `config/*.yaml` ignore rule. Detailed treatment: [../modules/sources/research-scripts.md](../modules/sources/research-scripts.md).

## `.gitignore` summary

Ignored: `.env`, virtualenvs (`.venv/`, `venv/`), `__pycache__`, `*.egg-info/`, `dist/`, `build/`, `.pytest_cache/`, `data/*.db` and its `-wal`/`-shm`/`-journal` siblings, `credentials*.json`, `token.json`, `secrets/`, all `*.json` except `tests/fixtures/**/*.json`, and `config/*.yaml` except `config/*.example.yaml`. The blanket `*.json` rule is what keeps the Google service-account file (`google-sa.json`, written by the pipeline) out of git. TSV files under `data/` are not ignored.

## Local setup recap

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"               # add ".[marketplaces]" for JobSpy
cp .env.example .env                  # fill in only what you need
cp config/profile.example.yaml config/profile.yaml   # edit; other configs are optional
pytest                                # offline suite
./start.sh                            # collect, digest, contacts, sheet-sync on data/jobs.db
```

Without Telegram credentials `digest` prints locally and the send is skipped unless `JOBDIGEST_REQUIRE_TELEGRAM` is set; without sheet credentials `sheet-sync` skips unless `JOBDIGEST_REQUIRE_SHEET` is set (`test_cli_contacts_and_sheet_sync_run_offline`, `test_deliver_required_missing_creds_ok_locally`).
