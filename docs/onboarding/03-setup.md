# 3. Development setup

## Prerequisites

* Python **3.11 or newer** (`requires-python = ">=3.11"`; CI uses 3.12).
* Git. A shell. Nothing else is required to run the tests: no database server, no API keys, no network.

## Install

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`-e` installs the package in editable mode so your source edits take effect immediately. `.[dev]` adds `pytest`. The package
pulls in `httpx`, `scrapling`, `playwright`, `gspread`, `psycopg[binary]`, `jinja2`, `pyyaml`, `feedparser` and a few others
(see [packaging](../infra/packaging-and-scripts.md)). Optional extras:

```bash
pip install -e ".[marketplaces]"        # JobSpy lane (LinkedIn/Indeed-style searches), off by default
python -m playwright install chromium   # only for portals configured with fetch: dynamic or stealth
```

## Verify the installation

```bash
python -m pytest -q
```

Expect roughly 730 passed and 1 skipped in about 30 seconds (the skip needs `pandas`). All tests are offline by design; see
[testing strategy](../testing/strategy.md).

Then run the guided, no-network pipeline demo:

```bash
python docs/onboarding/first_trace.py
```

## Configuration for a real local run

The tests and the demo use built-in example configuration. For a real run:

```bash
cp config/profile.example.yaml    config/profile.yaml     # edit: name, skills, city, ...
cp config/companies.example.yaml  config/companies.yaml   # edit: boards to fetch, set enabled: true
```

`config/models.yaml`, `portals.yaml` and `feeds.yaml` already exist in the repo. Create a `.env` file (git-ignored) with whatever you
need - all optional for local use:

```bash
OPENROUTER_API_KEY=...        # needed to score jobs
TELEGRAM_BOT_TOKEN=...        # create with @BotFather
TELEGRAM_CHAT_ID=...
GOOGLE_SHEET_ID=...           # only for sheet-sync
GOOGLE_SERVICE_ACCOUNT_JSON=google-sa.json
```

Without Telegram credentials the digest is printed and not sent; without sheet credentials `sheet-sync` skips; without
`DATABASE_URL` everything uses SQLite at `data/jobs.db`. If `config/profile.yaml` is missing the code silently uses the
example profile and logs `falling back to example config` - check for that line when results look odd. All variables are
listed in [configuration and secrets](../architecture/configuration-and-secrets.md).

## Running the pipeline locally

```bash
./start.sh
```

This runs `collect --portal-fetch http`, `digest`, `contacts` and `sheet-sync` against `data/jobs.db` with `DEBUG` logging. It
does not start the Telegram bot and skips browser-based portals. To run stages individually:

```bash
python -m jobdigest collect --db data/jobs.db --ats-only      # fast: ATS boards only
python -m jobdigest digest  --db data/jobs.db
python -m jobdigest.bot --once --db data/jobs.db              # answer pending button taps, then exit
python -m jobdigest.expand --limit 200                        # try board discovery on a small slice
```

## Editor tips

* `pyproject.toml` sets `pythonpath = ["src"]` for pytest, so tests import `jobdigest` without installation tricks.
* Set `JOBDIGEST_LOG_LEVEL=DEBUG` to see per-job decisions (`job insert ... hard_status=...`).
* The database is a normal SQLite file; open it with any SQLite browser (see [debugging](05-debugging.md) for useful queries).

Next: [4. Your first trace](04-first-trace.md).
